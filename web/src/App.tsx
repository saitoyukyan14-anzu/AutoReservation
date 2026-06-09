import { useEffect, useMemo, useState } from "react";
import CandidateEditor from "./components/CandidateEditor";
import Results from "./components/Results";
import { loadAll, matchCandidate } from "./lib/data";
import type { Candidate, EnrichedSlot } from "./types";

function newId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function todayISO(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function emptyCandidate(): Candidate {
  return { id: newId(), date: todayISO(), timeFrom: "", timeTo: "", minArea: "" };
}

export default function App() {
  const [slots, setSlots] = useState<EnrichedSlot[]>([]);
  const [updatedAt, setUpdatedAt] = useState("");
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [candidates, setCandidates] = useState<Candidate[]>([emptyCandidate()]);

  useEffect(() => {
    loadAll()
      .then(({ slots, updatedAt }) => {
        setSlots(slots);
        setUpdatedAt(updatedAt);
        setStatus("ready");
      })
      .catch(() => setStatus("error"));
  }, []);

  const update = (id: string, patch: Partial<Candidate>) =>
    setCandidates((cs) => cs.map((c) => (c.id === id ? { ...c, ...patch } : c)));
  const add = () => setCandidates((cs) => [...cs, emptyCandidate()]);
  const remove = (id: string) =>
    setCandidates((cs) => (cs.length > 1 ? cs.filter((c) => c.id !== id) : cs));

  const results = useMemo(
    () => candidates.map((c) => ({ candidate: c, matches: matchCandidate(slots, c) })),
    [candidates, slots]
  );
  const totalHits = results.reduce((n, r) => n + r.matches.length, 0);

  const updatedLabel = updatedAt
    ? new Date(updatedAt).toLocaleString("ja-JP", {
        month: "long",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";

  return (
    <div className="min-h-screen">
      <header className="border-b border-line bg-card/70 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-end justify-between gap-4 px-5 py-6">
          <div className="flex items-center gap-4">
            <div className="h-12 w-1.5 rounded-full bg-shu" />
            <div>
              <h1 className="font-display text-3xl font-bold tracking-wide text-ink">
                けやき空き
              </h1>
              <p className="mt-0.5 text-sm text-muted">
                希望の日時・広さを並べて、世田谷区の集会施設の空きをまとめて探す
              </p>
            </div>
          </div>
          <div className="hidden text-right sm:block">
            <p className="text-[11px] uppercase tracking-[0.16em] text-muted">最終更新</p>
            <p className="font-mono text-sm text-ink">{updatedLabel}</p>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-5 py-8">
        {status === "loading" && (
          <p className="py-24 text-center font-display text-xl text-muted">読み込み中…</p>
        )}

        {status === "error" && (
          <div className="rounded-xl border border-shu-soft bg-shu/5 px-6 py-16 text-center">
            <p className="font-display text-xl text-shu">データを読み込めませんでした</p>
            <p className="mt-2 text-sm text-muted">
              data/availability.json が配置されているか確認してください。
            </p>
          </div>
        )}

        {status === "ready" && (
          <div className="grid gap-8 lg:grid-cols-[320px_1fr]">
            <CandidateEditor
              candidates={candidates}
              onUpdate={update}
              onAdd={add}
              onRemove={remove}
            />
            <div>
              <div className="mb-5 flex items-baseline gap-2">
                <h2 className="font-display text-2xl font-semibold tracking-wide">検索結果</h2>
                <span className="font-mono text-sm text-muted">
                  合計 <span className="font-bold text-shu">{totalHits}</span> 件
                </span>
              </div>
              <Results results={results} />
            </div>
          </div>
        )}
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto max-w-6xl px-5 py-6 text-xs leading-relaxed text-muted">
          <p>
            空き状況は{" "}
            <a
              href="https://setagaya.keyakinet.net/Web/"
              target="_blank"
              rel="noreferrer"
              className="font-medium text-shu underline-offset-2 hover:underline"
            >
              けやきネット
            </a>{" "}
            から定期取得した参考情報です。実際の予約・最新状況は必ず公式サイトでご確認ください。
          </p>
          <p className="mt-1">広さ・定員は施設データベース（手動管理）に基づきます。</p>
        </div>
      </footer>
    </div>
  );
}
