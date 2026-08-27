/** Mirrors the backend's result contract. Every scientific scalar arrives inside this envelope, and
 *  the UI is built so that a value can never be rendered without what qualifies it. */
export type Provenance = "measured" | "chosen" | "inferred" | "refused";

export interface Refusal {
  code: string;
  message: string;
  remedy: string;
  evidence?: Record<string, number | string>;
}

export interface Q<T = number> {
  value: T | null;
  unit: string;
  provenance: Provenance;
  method?: string | null;
  ci95?: [number, number] | null;
  n?: number | null;
  refusal?: Refusal | null;
  note?: string | null;
}

export interface Spot {
  id: string;
  lane_index: number;
  status: "confirmed" | "candidate" | "rejected" | "proposed_unconfirmed" | "suppressed_streak";
  y_px: Q;
  y_frac: Q;
  rst: Q;
  amplitude_od: Q;
  area_od_px: Q;
  snr: Q;
  ensemble_agreement: Q;
  ensemble_n_total: number;
  ensemble_n_hit: number;
  ensemble_y_spread_px?: Q | null;
  peak_model: string;
  confidence: Q;
  flags: string[];
}

export interface Lane {
  index: number;
  label: string;
  label_provenance: string;
  x_center_px: Q;
  half_width_px: Q;
  is_empty: Q<boolean>;
  is_streaking: Q<boolean>;
  quantified: boolean;
  suppression: Refusal | null;
}

export interface Densitogram {
  lane_index: number;
  y_px: { start: number; stop: number; step: number };
  preview: number[];
  n_valid_columns: number;
}

export interface RunResult {
  run_id: string;
  created_at: string;
  status: "succeeded" | "refused" | "degraded";
  image: { sha256: string; width_px: number; height_px: number; original_filename: string | null };
  capture_qc: Record<string, Q | unknown> & { green_clip_frac_in_plate: Q; verdict: string };
  geometry: { rectified_shape: [number, number]; tilt_deg: Q };
  lanes: Lane[];
  reference: { origin_row_px: Q; origin_provenance: string; rst_anchor: { spot_id: string; lane_label: string } | null };
  photometry: { photometry_mode: "full" | "positions_only" | "refused"; sigma_od: Q };
  densitograms: Densitogram[];
  spots: Spot[];
  flags: { code: string; severity: string; message: string; remedy: string }[];
  refusals: Refusal[];
  provenance: Record<string, string | boolean | number | null>;
}

export type Verdict = "complete" | "in_progress" | "no_reaction_detected" | "cannot_conclude";

export interface ReactionValue {
  value: number | string | boolean | null;
  unit: string;
  provenance: Provenance;
  basis: string | null;
  interval: [number, number] | null;
  refusal: Refusal | null;
}

export interface Assignment {
  band_id: string;
  rst: number | null;
  identity: "starting_material" | "product" | "impurity" | "origin_residue" | "unassigned";
  label: string;
  basis: string;
  confidence: "high" | "medium" | "low";
  factors: string[];
  share_of_lane: ReactionValue;
  inherited: boolean | null;
  agreement: number | null;
}

export interface Reaction {
  verdict: Verdict;
  headline: string;
  plain_summary: string[];
  chemist_summary: string[];
  confidence: { grade: "high" | "medium" | "low"; factors: string[]; rule: string };
  anchors: Record<string, { band_id: string; rst: number | null; snr: number; agreement: number; lane: string } | null>;
  matrix_shift: { applied: ReactionValue; tolerance: number; agree: boolean | null; [k: string]: unknown };
  cospot: { available: boolean; reason?: string; alpha_S?: number; beta_R?: number; r_squared?: number; reading?: string };
  assignments: Assignment[];
  quantities: Record<string, ReactionValue | string | boolean>;
  impurities: { band_id: string; rst: number | null; inherited_from_starting_material: boolean; reading: string }[];
  caveats: string[];
  what_would_change_this: string[];
  next_experiment: string | null;
  refusals: Refusal[];
  glossary: Record<string, string>;
}
