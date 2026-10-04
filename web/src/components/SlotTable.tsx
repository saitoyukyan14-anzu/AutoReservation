import type { EnrichedSlot } from "../types";
import { formatDate, partsLabel } from "../lib/data";

interface Props {
  slots: EnrichedSlot[];
  /** 候補が単一日付でない場合などに日付も表示する */
  showDate?: boolean;
}

function ExternalIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="ml-1 inline-block h-3 w-3 shrink-0 align-[-1px] text-muted transition group-hover:text-shu"
    >
      <path d="M9.5 2.5h4v4" />
      <path d="M13.5 2.5 7.5 8.5" />
      <path d="M11.5 9.5v4h-9v-9h4" />
    </svg>
  );
}

function FacilityName({ slot }: { slot: EnrichedSlot }) {
  if (!slot.url) return <div className="font-medium leading-snug">{slot.facility}</div>;
  return (
    <a
      href={slot.url}
      target="_blank"
      rel="noopener noreferrer"
      title="施設の案内ページを新しいタブで開く"
      className="group font-medium leading-snug text-ink underline decoration-line decoration-1 underline-offset-4 transition hover:text-shu hover:decoration-shu"
    >
      {slot.facility}
      <ExternalIcon />
      <span className="sr-only">（新しいタブで開く）</span>
    </a>
  );
}

export default function SlotTable({ slots, showDate = false }: Props) {
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-card shadow-soft">
      <table className="w-full border-collapse text-left">
        <thead>
          <tr className="border-b border-line bg-paper/60 text-[11px] uppercase tracking-[0.14em] text-muted">
            <th className="px-4 py-2.5 font-bold">施設</th>
            <th className="hidden px-4 py-2.5 font-bold sm:table-cell">部屋</th>
            <th className="px-4 py-2.5 font-bold">空き時間</th>
          </tr>
        </thead>
        <tbody>
          {slots.map((s) => {
            const d = showDate ? formatDate(s.date) : null;
            const merged = partsLabel(s);
            return (
              <tr
                key={`${s.ward}-${s.date}-${s.facility}-${s.room}-${s.start}`}
                className="border-b border-line/60 last:border-0 transition hover:bg-shu/[0.03]"
              >
                <td className="px-4 py-3 align-top">
                  <span className="text-[11px] text-muted">{s.ward}</span>
                  <div>
                    <FacilityName slot={s} />
                  </div>
                  {/* 狭い画面では部屋を施設名の下に表示 */}
                  <div className="mt-0.5 text-sm text-muted sm:hidden">{s.room}</div>
                  {s.note && <div className="mt-1 text-[11px] text-muted">{s.note}</div>}
                </td>
                <td className="hidden px-4 py-3 align-top text-sm sm:table-cell">{s.room}</td>
                <td className="px-4 py-3 align-top">
                  {d && (
                    <div className="mb-1 font-mono text-[11px] text-muted">
                      {d.md}({d.wd})
                    </div>
                  )}
                  <span
                    title={merged ?? undefined}
                    className="inline-flex items-center gap-1.5 rounded-md border border-shu-soft bg-shu/[0.06] px-2 py-1 font-mono text-[13px] font-bold text-shu"
                  >
                    {s.start}
                    <span className="text-shu/50">→</span>
                    {s.end}
                  </span>
                  {merged && (
                    <div className="mt-1 text-[11px] leading-snug text-muted">{merged}</div>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
