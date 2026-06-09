import type {
  AvailabilityData,
  Candidate,
  EnrichedSlot,
  FacilitiesData,
  Facility,
} from "../types";

const base = import.meta.env.BASE_URL;

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
    ? await facRes.json()
    : { facilities: [] };

  const facMap = new Map<string, Facility>();
  for (const f of fac.facilities) facMap.set(key(f.ward, f.facility, f.room), f);

  const slots: EnrichedSlot[] = avail.slots.map((s) => {
    const f = facMap.get(key(s.ward, s.facility, s.room));
    return {
      ...s,
      area_sqm: f?.area_sqm ?? null,
      capacity: f?.capacity ?? null,
      note: f?.note ?? "",
    };
  });
  return { updatedAt: avail.updated_at, slots };
}

function key(ward: string, facility: string, room: string): string {
  return `${ward}|${facility}|${room}`;
}

export function toMinutes(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + (m || 0);
}

/**
 * 1件の候補に合致する空き枠を返す（ソート済み）。
 * - 日付：一致（候補日付が空なら日付で絞らない）
 * - 時間帯：希望レンジと空きバンドが「一部でも重なる」もの
 * - 広さ：指定時は 面積≧指定値（未登録は除外）／未指定なら全部含める
 */
export function matchCandidate(slots: EnrichedSlot[], c: Candidate): EnrichedSlot[] {
  const tf = c.timeFrom ? toMinutes(c.timeFrom) : null;
  const tt = c.timeTo ? toMinutes(c.timeTo) : null;
  const minArea = c.minArea ? Number(c.minArea) : null;

  const matched = slots.filter((s) => {
    if (c.date && s.date !== c.date) return false;

    if (tf !== null && toMinutes(s.end) <= tf) return false;
    if (tt !== null && toMinutes(s.start) >= tt) return false;

    if (minArea !== null && (s.area_sqm === null || s.area_sqm < minArea)) {
      return false;
    }
    return true;
  });
  return sortSlots(matched);
}

/** 候補の条件を1行のラベルにする（例: 6月13日(金)・13:00→17:00・40㎡以上）。 */
export function candidateLabel(c: Candidate): string {
  const parts: string[] = [];
  if (c.date) {
    const d = formatDate(c.date);
    parts.push(`${d.md}(${d.wd})`);
  } else {
    parts.push("日付未指定");
  }
  parts.push(c.timeFrom || c.timeTo ? `${c.timeFrom || "始発"}→${c.timeTo || "終了"}` : "終日");
  if (c.minArea) parts.push(`${c.minArea}㎡以上`);
  return parts.join("・");
}

export function sortSlots(slots: EnrichedSlot[]): EnrichedSlot[] {
  return [...slots].sort((a, b) => {
    if (a.date !== b.date) return a.date < b.date ? -1 : 1;
    if (a.start !== b.start) return toMinutes(a.start) - toMinutes(b.start);
    if (a.facility !== b.facility) return a.facility < b.facility ? -1 : 1;
    return a.room < b.room ? -1 : 1;
  });
}

const WEEKDAYS = ["日", "月", "火", "水", "木", "金", "土"];

export function formatDate(iso: string): { md: string; wd: string; sat: boolean; sun: boolean } {
  const d = new Date(`${iso}T00:00:00+09:00`);
  const wd = WEEKDAYS[d.getDay()];
  return {
    md: `${d.getMonth() + 1}月${d.getDate()}日`,
    wd,
    sat: d.getDay() === 6,
    sun: d.getDay() === 0,
  };
}
