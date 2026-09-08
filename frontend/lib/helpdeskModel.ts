// In-process port of Multi_agent_ticketing_system/company/helpdesk_model.py
// (HelpdeskModel). TF-IDF (word 1- & 2-grams, sublinear TF, L2 norm) + one
// logistic-regression head per IT class. Pure arithmetic, no dependencies —
// the weights live in helpdesk-model.json (~127 KB, bundled into the route).
import modelJson from "./helpdesk-model.json";

export type ItClass = "Access" | "Hardware" | "Network" | "Security" | "Software";

export const TEAMS: Record<ItClass, string> = {
  Access: "Desktop Support",
  Hardware: "Desktop Support",
  Network: "Network Team",
  Security: "Security Team",
  Software: "Application Support",
};

export interface HelpdeskPrediction {
  status: "classified" | "out_of_vocabulary";
  model_version: string;
  model_type: "tfidf";
  category: ItClass;
  suggested_team: string;
  confidence: number;
  scores: Record<ItClass, number>;
  accepted: boolean;
  threshold: number;
  advisory_only: true;
  production_validated: boolean;
}

const M = modelJson as {
  version: string;
  feature_mode: string;
  classes: ItClass[];
  vocabulary: Record<string, number>;
  idf: number[];
  coef: number[][];
  intercept: number[];
  threshold: number;
  production_validated?: boolean;
};

function normalize(text: string): string {
  let t = text.toLowerCase().trim();
  t = t.replace(/^user reported\s+/, "");
  t = t.replace(/\.?\s*further investigation needed\.?$/, "");
  const toks = t.match(/[a-z0-9]+/g) ?? [];
  return toks.join(" ");
}

// feature_mode is "word" for this model: unigrams + adjacent bigrams.
function features(text: string): string[] {
  const words = normalize(text).split(" ").filter(Boolean);
  const out: string[] = [];
  for (const w of words) out.push("w:" + w);
  for (let i = 0; i + 1 < words.length; i++) out.push("w:" + words[i] + " " + words[i + 1]);
  return out;
}

export function predictHelpdesk(text: string): HelpdeskPrediction {
  const counts = new Map<string, number>();
  for (const f of features(text)) counts.set(f, (counts.get(f) ?? 0) + 1);

  // sparse TF-IDF: value[idx] = (1 + ln(count)) * idf[idx]
  const values = new Map<number, number>();
  for (const [term, c] of counts) {
    const idx = M.vocabulary[term];
    if (idx === undefined) continue;
    values.set(idx, (1 + Math.log(c)) * M.idf[idx]);
  }

  let norm = 0;
  for (const v of values.values()) norm += v * v;
  norm = Math.sqrt(norm);

  const scores: number[] = M.classes.map((_, k) => {
    let s = M.intercept[k];
    if (norm) for (const [idx, v] of values) s += (M.coef[k][idx] * v) / norm;
    return s;
  });

  const maxS = Math.max(...scores);
  const exps = scores.map((s) => Math.exp(s - maxS));
  const denom = exps.reduce((a, b) => a + b, 0);
  const probs = exps.map((e) => e / denom);

  let best = 0;
  for (let k = 1; k < probs.length; k++) if (probs[k] > probs[best]) best = k;

  const category = M.classes[best];
  const confidence = probs[best];
  const scoreMap = Object.fromEntries(
    M.classes.map((c, k) => [c, probs[k]]),
  ) as Record<ItClass, number>;

  return {
    status: norm ? "classified" : "out_of_vocabulary",
    model_version: M.version,
    model_type: "tfidf",
    category,
    suggested_team: TEAMS[category],
    confidence,
    scores: scoreMap,
    accepted: Boolean(norm) && confidence >= M.threshold,
    threshold: M.threshold,
    advisory_only: true,
    production_validated: M.production_validated === true,
  };
}
