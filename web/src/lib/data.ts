import type {
  AvailabilityData,
  Candidate,
  EnrichedSlot,
  FacilitiesData,
  Facility,
  Slot,
  TimeRange,
} from "../types";

const base = import.meta.env.BASE_URL;

/** 前の枠の終了〜次の枠の開始がこの分数以内なら、表示上1行に統合する。 */
export const MERGE_GAP_MINUTES = 30;

export async function loadAll(): Promise<{
  updatedAt: string;
  slots: EnrichedSlot[];
}> {
  const [availRes, facRes] = await Promise.all([
    fetch(`${base}data/availability.json`),
    fetch(`${base}data/facilities.json`),
  ]);
  if (!availRes.ok) throw new Error("空き状況データを読み込めませんでした");
  const avail: AvailabilityData = await availRes.json();
  const fac: FacilitiesData = facRes.ok
    ? await facRes.json().catch(() => ({ facilities: [] }))
    : { facilities: [] };

  const facMap = new Map<string, Facility>();
  for (const f of fac.facilities ?? []) {
    const k = facilityKey(f.ward, f.facility);
    if (!facMap.has(k)) facMap.set(k, f);
  }

  // 統合は全スロットに対して先に行う（候補の時間帯フィルタはその後）
  const slots: EnrichedSlot[] = mergeSlots(avail.slots).map((s) => {
    const f = facMap.get(facilityKey(s.ward, s.facility));
    return {
      ...s,
      url: safeUrl(f?.url),
      note: f?.note ?? "",
    };
  });
  return { updatedAt: avail.updated_at, slots };
}

function facilityKey(ward: string, facility: string): string {
  return `${ward}\t${facility}`;
}

/** http(s) の URL だけをリンクとして扱う（それ以外は null）。 */
function safeUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  const u = url.trim();
  return /^https?:\/\//i.test(u) ? u : null;
}

export function toMinutes(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + (m || 0);
}

/**
 * 同じ区・日付・施設・部屋で、前の枠の end と次の枠の start の間隔が
 * MERGE_GAP_MINUTES 以内の枠を連鎖的に1行へ統合する（表示上のみ）。
 * 例: 12:30–14:30 と 15:00–17:00 → 12:30–17:00（parts に元の2枠を保持）
 */
export function mergeSlots(slots: Slot[]): (Slot & { parts: TimeRange[] })[] {
  const groups = new Map<string, Slot[]>();
  for (const s of slots) {
    const k = `${s.ward}\t${s.date}\t${s.facility}\t${s.room}`;
    const g = groups.get(k);
    if (g) g.push(s);
    else groups.set(k, [s]);
  }

  const out: (Slot & { parts: TimeRange[] })[] = [];
  for (const g of groups.values()) {
    g.sort(
      (a, b) => toMinutes(a.start) - toMinutes(b.start) || toMinutes(a.end) - toMinutes(b.end)
    );
    let cur: (Slot & { parts: TimeRange[] }) | null = null;
    for (const s of g) {
      if (cur && toMinutes(s.start) - toMinutes(cur.end) <= MERGE_GAP_MINUTES) {
        const last = cur.parts[cur.parts.length - 1];
        if (last.start !== s.start || last.end !== s.end) {
          cur.parts.push({ start: s.start, end: s.end });
        }
        if (toMinutes(s.end) > toMinutes(cur.end)) cur.end = s.end;
      } else {
        cur = { ...s, parts: [{ start: s.start, end: s.end }] };
        out.push(cur);
      }
    }
  }
  return out;
}

/** 統合行の内訳（例: "12:30–14:30 / 15:00–17:00 の2枠（予約は枠ごと）"）。統合されていなければ null。 */
export function partsLabel(s: { parts: TimeRange[] }): string | null {
  if (s.parts.length < 2) return null;
  const list = s.parts.map((p) => `${p.start}–${p.end}`).join(" / ");
  return `${list} の${s.parts.length}枠（予約は枠ごと）`;
}

/** 読み込んだ空き枠に含まれる区の一覧（重複なし・ソート済み）。 */
export function listWards(slots: Slot[]): string[] {
  return [...new Set(slots.map((s) => s.ward))].sort((a, b) => a.localeCompare(b, "ja"));
}

/**
 * 1件の候補に合致する空き枠（統合済み）を返す（ソート済み）。
 * - 日付：一致（候補日付が空なら日付で絞らない）
 * - 時間帯：希望レンジと統合後の空きバンドが「一部でも重なる」もの
 * - 区：選択されていればそのいずれか／未選択なら全区
 */
export function matchCandidate(slots: EnrichedSlot[], c: Candidate): EnrichedSlot[] {
  const tf = c.timeFrom ? toMinutes(c.timeFrom) : null;
  const tt = c.timeTo ? toMinutes(c.timeTo) : null;
  const wards = c.wards.length > 0 ? new Set(c.wards) : null;

  const matched = slots.filter((s) => {
    if (c.date && s.date !== c.date) return false;
    if (wards && !wards.has(s.ward)) return false;

    if (tf !== null && toMinutes(s.end) <= tf) return false;
    if (tt !== null && toMinutes(s.start) >= tt) return false;
    return true;
  });
  return sortSlots(matched);
}

/** 候補の条件を1行のラベルにする（例: 6月13日(金)・13:00→17:00・世田谷区、目黒区）。 */
export function candidateLabel(c: Candidate): string {
  const parts: string[] = [];
  if (c.date) {
    const d = formatDate(c.date);
    parts.push(`${d.md}(${d.wd})`);
  } else {
    parts.push("日付未指定");
  }
  parts.push(c.timeFrom || c.timeTo ? `${c.timeFrom || "始発"}→${c.timeTo || "終了"}` : "終日");
  if (c.wards.length > 0) {
    parts.push(
      c.wards.length <= 3
        ? c.wards.join("、")
        : `${c.wards.slice(0, 2).join("、")}ほか${c.wards.length - 2}区`
    );
  }
  return parts.join("・");
}

export function sortSlots(slots: EnrichedSlot[]): EnrichedSlot[] {
  return [...slots].sort((a, b) => {
    if (a.date !== b.date) return a.date < b.date ? -1 : 1;
    if (a.start !== b.start) return toMinutes(a.start) - toMinutes(b.start);
    if (a.ward !== b.ward) return a.ward < b.ward ? -1 : 1;
    if (a.facility !== b.facility) return a.facility < b.facility ? -1 : 1;
    return a.room < b.room ? -1 : 1;
  });
}

const WEEKDAYS = ["日", "月", "火", "水", "木", "金", "土"];

export function formatDate(iso: string): { md: string; wd: string; sat: boolean; sun: boolean } {
  // UTC として解釈し UTC の getter で読む（閲覧環境のタイムゾーンで日付がずれないように）
  const d = new Date(`${iso}T00:00:00Z`);
  const day = d.getUTCDay();
  return {
    md: `${d.getUTCMonth() + 1}月${d.getUTCDate()}日`,
    wd: WEEKDAYS[day],
    sat: day === 6,
    sun: day === 0,
  };
}
