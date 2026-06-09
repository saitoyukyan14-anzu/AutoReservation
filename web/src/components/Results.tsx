import type { Candidate, EnrichedSlot } from "../types";
import { candidateLabel } from "../lib/data";
import SlotTable from "./SlotTable";

interface Props {
  results: { candidate: Candidate; matches: EnrichedSlot[] }[];
}

export default function Results({ results }: Props) {
  return (
    <div className="space-y-9">
      {results.map(({ candidate, matches }, i) => {
        const showDate = !candidate.date; // 日付未指定の候補は各行に日付を表示
        return (
          <section key={candidate.id}>
            <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-line pb-2">
              <span className="grid h-6 w-6 place-items-center rounded-full bg-shu/10 font-mono text-xs font-bold text-shu">
                {i + 1}
              </span>
              <h3 className="font-display text-xl font-semibold">
                {candidateLabel(candidate)}
              </h3>
              <span className="font-mono text-xs text-muted">
                {matches.length > 0 ? `${matches.length}件の空き` : "空きなし"}
              </span>
            </div>

            {matches.length > 0 ? (
              <SlotTable slots={matches} showDate={showDate} />
            ) : (
              <p className="rounded-xl border border-dashed border-line bg-card/50 px-5 py-8 text-center text-sm text-muted">
                この条件に合う空きは見つかりませんでした。時間帯や広さをゆるめてみてください。
              </p>
            )}
          </section>
        );
      })}
    </div>
  );
}
