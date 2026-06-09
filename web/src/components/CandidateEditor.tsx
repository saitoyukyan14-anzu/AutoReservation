import type { Candidate } from "../types";

interface Props {
  candidates: Candidate[];
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
const labelCls =
  "mb-1 block text-[11px] font-bold uppercase tracking-[0.16em] text-muted";

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

export default function CandidateEditor({
  candidates,
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

            <div>
              <label className={labelCls}>広さ（㎡以上）</label>
              <input
                type="number"
                min={0}
                inputMode="numeric"
                placeholder="例: 40（任意）"
                className={inputCls}
                value={c.minArea}
                onChange={(e) => onUpdate(c.id, { minArea: e.target.value })}
              />
            </div>
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
