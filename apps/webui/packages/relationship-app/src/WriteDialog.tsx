import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Button, Modal, useUnsavedChanges } from '@icarus/ui';
import { RelaError } from './rela';

/** One explicit REST operation per submission; transport failure never replays a write. */
export default function WriteDialog({
  title,
  children,
  dirty,
  submit,
  onClose,
  onSaved,
  saveText = '保存',
}: {
  title: string;
  children: ReactNode;
  dirty: boolean;
  submit: () => Promise<unknown>;
  onClose: () => void;
  onSaved: () => void;
  saveText?: string;
}) {
  const [busy, setBusy] = useState(false);
  const working = useRef(false);
  const [error, setError] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const guard = useUnsavedChanges(dirty || busy || uncertain);
  function close() {
    if (!working.current) guard.requestLeave(onClose);
  }
  useEffect(() => {
    function escape(event: KeyboardEvent) {
      if (event.key !== 'Escape' || event.isComposing) return;
      // Semi registers every modal on document; consume Escape for this topmost editor.
      event.preventDefault();
      event.stopImmediatePropagation();
      if (guard.confirming) guard.keepEditing();
      else if (!working.current) guard.requestLeave(onClose);
    }
    document.addEventListener('keydown', escape, true);
    return () => document.removeEventListener('keydown', escape, true);
  }, [guard, onClose]);
  return (
    <>
      <Modal
        visible
        title={title}
        width={560}
        className="rela-write-modal"
        footer={null}
        onCancel={close}
        maskClosable={false}
        closeOnEsc={false}
      >
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            if (working.current || uncertain) return;
            working.current = true;
            setBusy(true);
            setError('');
            try {
              await submit();
              guard.markSaved();
              onSaved();
            } catch (failure) {
              const unknown = failure instanceof RelaError && failure.uncertain;
              setUncertain(unknown);
              setError(
                unknown
                  ? '写入结果待核对。请先关闭表单并刷新相关记录，确认是否已生效。'
                  : failure instanceof Error
                    ? failure.message
                    : '操作失败，请检查输入。',
              );
            } finally {
              working.current = false;
              setBusy(false);
            }
          }}
        >
          <fieldset disabled={busy} className="rela-form-fields">
            {children}
          </fieldset>
          {error && (
            <div className="rela-error" role="alert">
              {error}
            </div>
          )}
          {uncertain && (
            <Button
              theme="borderless"
              onClick={() => {
                setUncertain(false);
                setError('');
              }}
            >
              已核对结果，允许再次提交
            </Button>
          )}
          <div className="rela-form-actions">
            <Button onClick={close} disabled={busy}>
              取消
            </Button>
            <Button theme="solid" htmlType="submit" disabled={busy || uncertain} loading={busy}>
              {saveText}
            </Button>
          </div>
        </form>
      </Modal>
      <Modal
        visible={guard.confirming}
        closeOnEsc={false}
        title="放弃未保存的更改？"
        onCancel={guard.keepEditing}
        onOk={guard.discardChanges}
        okText="放弃"
        cancelText="继续编辑"
      >
        未保存的输入会丢失；已提交的请求不会因此撤销。
      </Modal>
    </>
  );
}
