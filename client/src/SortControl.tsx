import { ArrowUpDown } from "lucide-react";

/** One sort choice for a list, named for what that list holds (D-120). */
export function SortControl<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: readonly (readonly [T, string])[];
  onChange: (value: T) => void;
}) {
  return (
    <label className="sort-control">
      <ArrowUpDown size={18} aria-hidden="true" />
      <span>Sort</span>
      <select value={value} onChange={(e) => onChange(e.target.value as T)}>
        {options.map(([key, label]) => (
          <option key={key} value={key}>
            {label}
          </option>
        ))}
      </select>
    </label>
  );
}

/** Alphabetical as a person files it: case, accents and a leading "The "
 * don't count, and numbers read as numbers. */
const collator = new Intl.Collator("en-AU", {
  sensitivity: "base",
  numeric: true,
});
const filed = (text: string | undefined) =>
  (text || "").trim().replace(/^the\s+(?=\S)/i, "");
export const byName = (a: string | undefined, b: string | undefined) =>
  collator.compare(filed(a), filed(b));
