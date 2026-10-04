import { useId } from "react";
import type { Candidate } from "../types";

interface Props {
  candidates: Candidate[];
  /** 選択肢にする区の一覧（読み込んだ空き枠から生成、ソート済み） */
  wardOptions: string[];
  onUpdate: (id: string, patch: Partial<Candidate>) => void;
  onAdd: () => void;
  onRemove: (id: string) => void;
}

const TIME_OPTIONS = (() => {
  const out: string[] = [];
  for (let h = 8; h <= 22; h++) {
    out.push(`${h}:00`);
    if (h < 22) out.push(`${h}:30`);
  }
  return out;
})();

const inputCls =
  "w-full rounded-md border border-line bg-card px-3 py-2 text-sm text-ink shadow-inner outline-none transition focus:border-shu focus:ring-2 focus:ring-shu/20";
const labelTextCls = "block text-[11px] font-bold uppercase tracking-[0.16em] text-muted";
const labelCls = `mb-1 ${labelTextCls}`;

function TimeSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <select className={inputCls} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">指定なし</option>
      {TIME_OPTIONS.map((t) => (
        <option key={t} value={t}>
          {t}
        </option>
      ))}
    </select>
  );
}

function WardPicker({
  options,
  value,
  onChange,
}: {
  options: string[];
  value: string[];
  onChange: (v: string[]) => void;
}) {
  const labelId = useId();
  const selected = new Set(value);
  const toggle = (w: string) => {
    const next = new Set(selected);
    if (next.has(w)) next.delete(w);
    else next.add(w);
    // 表示順（ソート済みの選択肢順）で保持する
    onChange(options.filter((o) => next.has(o)));
  };

  return (
    <div role="group" aria-labelledby={labelId}>
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <span id={labelId} className={labelTextCls}>
          区（複数選択可）
        </span>
        {value.length > 0 ? (
          <button
            type="button"
            onClick={() => onChange([])}
            className="text-[11px] text-muted underline-offset-2 transition hover:text-shu hover:underline"
          >
            選択を解除
          </button>
        ) : (
          <span className="text-[11px] text-muted">未選択＝すべての区</span>
        )}
      </div>
      {options.length === 0 ? (
        <p className="text-xs text-muted">区のデータがありません</p>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {options.map((w) => {
            const on = selected.has(w);
            return (
              <label
                key={w}
                className={
                  "inline-flex cursor-pointer select-none items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition " +
                  (on
                    ? "border-shu bg-shu/10 font-bold text-shu"
                    : "border-line bg-card text-ink hover:border-shu/60")
                }
              >
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5 accent-shu"
                  checked={on}
                  onChange={() => toggle(w)}
                />
                {w}
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function CandidateEditor({
  candidates,
  wardOptions,
  onUpdate,
  onAdd,
  onRemove,
}: Props) {
  return (
    <aside className="space-y-4 lg:sticky lg:top-6 lg:max-h-[calc(100vh-3rem)] lg:overflow-y-auto lg:pr-1">
      <div className="flex items-baseline justify-between">
        <h2 className="font-display text-xl font-semibold tracking-wide">希望候補</h2>
        <span className="text-xs text-muted">複数入力できます</span>
      </div>

      {candidates.map((c, i) => (
        <div key={c.id} className="rounded-xl border border-line bg-card shadow-soft">
          <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
            <span className="flex items-center gap-2 font-display text-sm font-semibold">
              <span className="grid h-6 w-6 place-items-center rounded-full bg-shu/10 font-mono text-xs font-bold text-shu">
                {i + 1}
              </span>
              候補 {i + 1}
            </span>
            {candidates.length > 1 && (
              <button
                onClick={() => onRemove(c.id)}
                aria-label="この候補を削除"
                className="rounded px-2 py-1 text-xs text-muted transition hover:bg-shu/10 hover:text-shu"
              >
                ✕ 削除
              </button>
            )}
          </div>

          <div className="space-y-3.5 px-4 py-4">
            <div>
              <label className={labelCls}>日付</label>
              <input
                type="date"
                className={inputCls}
                value={c.date}
                onChange={(e) => onUpdate(c.id, { date: e.target.value })}
              />
            </div>

            <div>
              <label className={labelCls}>時間（から / まで）</label>
              <div className="flex items-center gap-2">
                <TimeSelect value={c.timeFrom} onChange={(v) => onUpdate(c.id, { timeFrom: v })} />
                <span className="text-muted">–</span>
                <TimeSelect value={c.timeTo} onChange={(v) => onUpdate(c.id, { timeTo: v })} />
              </div>
            </div>

            <WardPicker
              options={wardOptions}
              value={c.wards}
              onChange={(v) => onUpdate(c.id, { wards: v })}
            />
          </div>
        </div>
      ))}

      <button
        onClick={onAdd}
        className="w-full rounded-xl border border-dashed border-line bg-paper/50 px-3 py-3 text-sm font-medium text-muted transition hover:border-shu hover:text-shu"
      >
        ＋ 候補を追加
      </button>
    </aside>
  );
}
