"""Synthetic business fixtures, not prewritten tool sequences or rule source."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    name: str
    platform: str
    source: str | int
    initial: str
    followup_platform: str
    followup_source: str | int
    followup: str
    criterion: str
    negative: str
    quoted: str
    unrelated: str


def scenarios():
    return [
        Scenario('work_review', 'qq', 30001,
            '请帮我跟进仓储接口评审，目前还没有最终结论。后续可能从飞书来源 feishu:synthetic-tenant:chat:oc_review_synthetic 收到消息，也请一起关注。只整理和跟进，不要替我发消息。',
            'feishu', 'oc_review_synthetic', '审核同事已签字放行，仓储接口可以进入联调。这次评审跟进可以结束了。',
            '本条消息是否实际宣告仓储接口评审已经通过、获准进入联调？否定、假设、引用规则不算。',
            '仓储接口尚未评审通过，仍在修改。', '只有收到“评审通过”时才通知，这只是规则说明。', '中午一起吃饭吗？'),
        Scenario('life_health', 'feishu', 'oc_health_synthetic',
            '请帮我跟进家庭体检预约，目前等医院回复。收到正式预约结果后整理注意事项；不需要真的预约或发消息。',
            'feishu', 'oc_health_synthetic', '医院已确认：下周二九点体检，需空腹；预约已经落实，这件跟进可以结束。',
            '本条消息是否实际确认家庭体检预约已落实？尚未确认、假设、引用规则不算。',
            '还没有收到医院的体检确认，继续等。', '“体检确认”是我希望收到的消息类型，并不是医院回复。', '今天降温了，多穿一点。'),
        Scenario('work_release', 'qq', 30002,
            '帮我盯一下库存服务的发布结果。现在只到了预发布验证阶段，等生产上线且健康检查正常后，这项跟进才算完成。不要操作服务器。',
            'qq', 30002, '库存服务已完成生产发布，健康检查和错误率均正常，这次发布跟进可以关闭。',
            '消息是否实际确认库存服务已生产上线且健康检查正常？预发布、计划、否定不算。',
            '库存服务尚未生产发布，健康检查也没做完。', '发布规范要求“生产上线且健康检查正常”，这不是发布结果。', '代码评审的排期下周再说。'),
        Scenario('life_delivery', 'feishu', 'oc_delivery_synthetic',
            '帮我跟进洗衣机送货，现在还在安排车辆。收到确切送达消息后提醒我核对外观和配件；不要联系商家。',
            'feishu', 'oc_delivery_synthetic', '洗衣机刚刚已送到家门口，外包装完整，请核对配件。物流跟进到此结束。',
            '消息是否实际确认洗衣机已经送达家中？安排车辆、预计送达、引用消息模板不算。',
            '洗衣机还没送到，司机还在路上。', '商家说他们会用“已送达”模板通知，但目前没有发货结果。', '明天想去公园散步。'),
        Scenario('work_payment', 'qq', 30003,
            '请帮我跟进供应商账单的付款状态。目前只是提交了申请，等财务确认到账后再结束跟进。只记录信息，不执行任何付款。',
            'qq', 30003, '财务核对了回单，供应商已确认款项到账，账单付款跟进可以结束。',
            '消息是否实际确认供应商已收到这笔款项？付款申请、计划、未到账和引用条件不算。',
            '付款申请已交，但供应商尚未确认到账。', '结案条件是“供应商确认到账”，现在只是解释条件。', '会议室下午有人用吗？'),
        Scenario('life_repair', 'feishu', 'oc_repair_synthetic',
            '请帮我跟进空调维修。师傅说先排查，只有修好并试机正常后才结束。不要替我预约或付款。',
            'feishu', 'oc_repair_synthetic', '师傅已更换故障件，空调试机半小时正常制冷，维修已完成，可以结束跟进。',
            '消息是否实际确认空调已修复且试机正常？排查、维修计划、否定和引用条件不算。',
            '空调还没修好，试机仍然报故障。', '用户要求“修好并试机正常”才结束，这不是完成通知。', '今天晚上看什么电影？'),
    ]


def semantic_cases():
    return [dict(scenario=case.name, category=category, text=text, criterion=case.criterion, expected=expected)
            for case in scenarios()
            for category, text, expected in [('positive_paraphrase', case.followup, True),
                                              ('negative', case.negative, False),
                                              ('quoted', case.quoted, False),
                                              ('unrelated', case.unrelated, False)]]


def discovery_cases():
    """Initial requests and routing boundaries, labelled before any model execution."""
    cases = [dict(scenario=case.name, category='initial', text=case.initial,
                  candidates=[], expected_action='new') for case in scenarios()]
    review = {'id': 'review-active', 'title': '仓储接口评审', 'goal': '跟进仓储接口评审',
              'progress': '等待审核意见', 'lifecycle': 'active'}
    cases.extend([
        dict(scenario='work_review', category='existing_continuation',
             text='仓储接口评审还在等审核意见，请继续关注刚才那件事。', candidates=[review],
             expected_action='existing', expected_channel_id='review-active'),
        dict(scenario='work_review', category='same_source_new_topic',
             text='另外请帮我跟进洗衣机送货，目前还在安排车辆。', candidates=[review], expected_action='new'),
        dict(scenario='work_review', category='quoted_request',
             text='“请帮我跟进接口评审”只是示例用语，不是我现在的请求。', candidates=[], expected_action='ignore'),
        dict(scenario='work_review', category='negated_request',
             text='不需要你帮忙跟进，这里只是在说明一个假设。', candidates=[], expected_action='ignore'),
        dict(scenario='work_review', category='chatter',
             text='今天中午吃什么？', candidates=[], expected_action='ignore'),
        dict(scenario='work_review', category='ambiguous_reference', text='那个再跟一下。',
             candidates=[review, {'id': 'delivery-active', 'title': '洗衣机送货', 'goal': '等待送货',
                                  'progress': '车辆安排中', 'lifecycle': 'active'}], expected_action='defer'),
    ])
    return cases
