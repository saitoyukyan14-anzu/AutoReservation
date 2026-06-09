import type { EnrichedSlot } from "../types";
import { formatDate } from "../lib/data";

interface Props {
  slots: EnrichedSlot[];
  /** 候補が単一日付でない場合などに日付も表示する */
  showDate?: boolean;
}

function Area({ slot }: { slot: EnrichedSlot }) {
  if (slot.area_sqm == null && slot.capacity == null)
    return <span className="text-line">—</span>;
  return (
    <span className="font-mono text-[13px] text-ink">
      {slot.area_sqm != null && <>{slot.area_sqm}㎡</>}
      {slot.capacity != null && (
        <span className="text-muted">
          {slot.area_sqm != null ? " / " : ""}
          {slot.capacity}人
        </span>
      )}
    </span>
  );
}

export default function SlotTable({ slots, showDate = false }: Props) {
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-card shadow-soft">
      <table className="w-full border-collapse text-left">
        <thead>
          <tr className="border-b border-line bg-paper/60 text-[11px] uppercase tracking-[0.14em] text-muted">
            <th className="px-4 py-2.5 font-bold">施設</th>
            <th className="px-4 py-2.5 font-bold">部屋</th>
            <th className="px-4 py-2.5 font-bold">空き時間</th>
            <th className="px-4 py-2.5 font-bold">広さ / 定員</th>
          </tr>
        </thead>
        <tbody>
          {slots.map((s, i) => {
            const d = showDate ? formatDate(s.date) : null;
            return (
              <tr
                key={`${s.date}-${s.facility}-${s.room}-${s.start}-${i}`}
                className="border-b border-line/60 last:border-0 transition hover:bg-shu/[0.03]"
              >
                <td className="px-4 py-3 align-top">
                  <span className="text-[11px] text-muted">{s.ward}</span>
                  <div className="font-medium leading-snug">{s.facility}</div>
                </td>
                <td className="px-4 py-3 align-top text-sm">{s.room}</td>
                <td className="px-4 py-3 align-top">
                  {d && (
                    <div className="mb-1 font-mono text-[11px] text-muted">
                      {d.md}({d.wd})
                    </div>
                  )}
                  <span className="inline-flex items-center gap-1.5 rounded-md border border-shu-soft bg-shu/[0.06] px-2 py-1 font-mono text-[13px] font-bold text-shu">
                    {s.start}
                    <span className="text-shu/50">→</span>
                    {s.end}
                  </span>
                  {s.note && <div className="mt-1 text-[11px] text-muted">{s.note}</div>}
                </td>
                <td className="px-4 py-3 align-top">
                  <Area slot={s} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
