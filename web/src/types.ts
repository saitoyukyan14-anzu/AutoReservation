export interface Slot {
  ward: string;
  facility: string;
  room: string;
  date: string; // YYYY-MM-DD
  start: string; // H:MM
  end: string; // H:MM
}

export interface AvailabilityData {
  updated_at: string;
  date_from: string;
  date_to: string;
  slots: Slot[];
}

/** 施設データベースの1行（1行=1施設）。空き状況とは ward + facility で突合する。 */
export interface Facility {
  ward: string;
  facility: string;
  url: string | null;
  note: string;
}

export interface FacilitiesData {
  facilities: Facility[];
}

export interface TimeRange {
  start: string; // H:MM
  end: string; // H:MM
}

/**
 * 表示用の1行。同じ区・日付・施設・部屋で連続する枠を1つに統合し、施設情報を結合したもの。
 * start / end は統合後の範囲、parts は統合元の枠（統合されていなければ1要素）。
 */
export interface EnrichedSlot extends Slot {
  parts: TimeRange[];
  url: string | null;
  note: string;
}

/** 希望する1件の候補（日付・時間帯・区）。複数入力してOR検索する。 */
export interface Candidate {
  id: string;
  date: string; // YYYY-MM-DD（単一日付）
  timeFrom: string; // "HH:MM" / ""
  timeTo: string; // "HH:MM" / ""
  wards: string[]; // 空配列 = 全区
}
