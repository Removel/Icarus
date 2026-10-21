"""Versioned YAML rule loading and deterministic mechanical evaluation."""

from hashlib import sha256
from pathlib import Path

import yaml
from pydantic import ValidationError

from ..schemas.judgement import Rule, RuleSet

OPERATORS = {'eq', 'ne', 'in', 'contains_any', 'contains_all', 'gte', 'lte', 'exists'}


class RuleValidationError(ValueError):
    pass


def validate_when(value, known_facts):
    if not isinstance(value, dict):
        raise RuleValidationError('when must be an object')
    for name, condition in value.items():
        if name in ('all', 'any'):
            if not isinstance(condition, list) or not condition:
                raise RuleValidationError('all/any require nonempty lists')
            for child in condition:
                validate_when(child, known_facts)
        elif name == 'not':
            validate_when(condition, known_facts)
        else:
            if name not in known_facts:
                raise RuleValidationError(f'Unknown fact: {name}')
            if isinstance(condition, dict):
                if not condition or set(condition) - OPERATORS:
                    raise RuleValidationError('Unknown rule operator')
                for op, expected in condition.items():
                    if op == 'exists' and type(expected) is not bool:
                        raise RuleValidationError('exists requires boolean')
                    if op in ('in', 'contains_any', 'contains_all') and not isinstance(expected, list):
                        raise RuleValidationError('Membership operators require lists')


def load_rule_set(path: Path, *, known_facts: set[str]) -> RuleSet:
    raw = path.read_bytes()
    try:
        value = yaml.safe_load(raw)
        if not isinstance(value, dict) or set(value) - {'source', 'version', 'default', 'rules'}:
            raise RuleValidationError('Invalid rule document')
        if type(value.get('version')) is not int or value['version'] < 1:
            raise RuleValidationError('version must be a positive integer')
        default = value['default']
        if set(default) != {'decision', 'reason'} or default['decision'] != 'ignore':
            raise RuleValidationError('Default must ignore')
        if not isinstance(default['reason'], str):
            raise RuleValidationError('Default reason must be text')
        rules, identifiers = [], set()
        for entry in value['rules']:
            if type(entry.get('priority', 0)) is not int or type(entry.get('semantic', False)) is not bool:
                raise RuleValidationError('Invalid rule priority/semantic type')
            rule = Rule.model_validate(entry)
            if not rule.id or rule.id == 'none' or rule.id in identifiers:
                raise RuleValidationError('Duplicate or reserved rule ID')
            identifiers.add(rule.id)
            validate_when(rule.when, known_facts)
            rules.append(rule)
        ordered = sorted(rules, key=lambda rule: -rule.priority)
        return RuleSet(source=value['source'], version=value['version'], digest=sha256(raw).hexdigest(),
                       mechanical=tuple(rule for rule in ordered if not rule.semantic),
                       semantic=tuple(rule for rule in ordered if rule.semantic), default_reason=default['reason'])
    except (KeyError, TypeError, ValidationError, yaml.YAMLError) as error:
        raise RuleValidationError('Invalid rule document') from error


def matches(when, facts):
    for name, condition in when.items():
        if name == 'all':
            matched = all(matches(child, facts) for child in condition)
        elif name == 'any':
            matched = any(matches(child, facts) for child in condition)
        elif name == 'not':
            matched = not matches(condition, facts)
        else:
            checks = condition if isinstance(condition, dict) else {'eq': condition}
            matched = all(compare(op, facts.get(name), expected, present=name in facts)
                          for op, expected in checks.items())
        if not matched:
            return False
    return True


def compare(operator, actual, expected, *, present):
    if operator == 'exists':
        return present == expected
    if not present:
        return False
    try:
        if operator == 'eq': return actual == expected
        if operator == 'ne': return actual != expected
        if operator == 'in': return actual in expected
        if operator == 'contains_any': return any(value in actual for value in expected)
        if operator == 'contains_all': return all(value in actual for value in expected)
        if operator == 'gte': return actual >= expected
        if operator == 'lte': return actual <= expected
    except TypeError:
        raise RuleValidationError('Incompatible fact and comparison types') from None
    raise RuleValidationError('Unknown operator')


def evaluate_mechanical(rules, facts):
    return next((rule for rule in rules.mechanical if matches(rule.when, facts)), None)


def candidate_semantic_rules(rules, facts):
    return tuple(rule for rule in rules.semantic if matches(rule.when, facts))
