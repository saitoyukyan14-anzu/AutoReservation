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

export interface Facility {
  ward: string;
  facility: string;
  room: string;
  area_sqm: number | null;
  capacity: number | null;
  note: string;
}

export interface FacilitiesData {
  facilities: Facility[];
}

/** 空き時間に広さ情報を結合した、表示用の1行。 */
export interface EnrichedSlot extends Slot {
  area_sqm: number | null;
  capacity: number | null;
  note: string;
}

/** 希望する1件の候補（日付・時間帯・広さ）。複数入力してOR検索する。 */
export interface Candidate {
  id: string;
  date: string; // YYYY-MM-DD（単一日付）
  timeFrom: string; // "HH:MM" / ""
  timeTo: string; // "HH:MM" / ""
  minArea: string; // 数値文字列（㎡以上）/ ""
}
