/**
 * textDecoder - Unified text normalization layer.
 *
 * ALL text entering the UI MUST pass through normalizeText().
 * Handles: \uXXXX escapes, double-encoded JSON, non-string values.
 *
 * Usage: import { normalizeText } from "../utils/textDecoder";
 */

/**
 * Safely decode any text value into a clean UTF-8 string.
 *
 * Handles:
 *   - \uXXXX escape sequences  →  decoded Chinese characters
 *   - Double-encoded JSON      →  parsed and decoded
 *   - Non-string types         →  safe string conversion
 *   - Null/undefined           →  empty string
 *
 * Never throws. Always returns a string.
 */
export function normalizeText(val) {
  if (val == null) return "";

  // If it's already a clean string, decode any escapes
  if (typeof val === "string") {
    return decodeUnicodeEscapes(val);
  }

  // If it's a number/boolean, convert directly
  if (typeof val === "number" || typeof val === "boolean") {
    return String(val);
  }

  // If it's an array, join elements
  if (Array.isArray(val)) {
    return val.map(normalizeText).join(", ");
  }

  // If it's an object, try common text fields first, then stringify
  if (typeof val === "object") {
    const text =
      val.display_text ||
      val.reason ||
      val.action ||
      val.text ||
      val.label ||
      val.name ||
      safeStringify(val);
    return normalizeText(text);
  }

  return String(val);
}

/**
 * Decode \uXXXX escape sequences and double-encoded JSON in a string.
 *
 * Examples:
 *   "\\u8F6C\\u5316"          →  "转化"
 *   '"\\u8F6C\\u5316"'        →  "转化"  (double-encoded JSON string)
 *   "normal text"              →  "normal text"
 */
function decodeUnicodeEscapes(str) {
  // Quick check: does it have any \uXXXX?
  if (!str.includes("\\u")) return str;

  // Step 1: Try to detect double-encoded JSON (entire string is a JSON string)
  // A double-encoded string looks like: "\"\\u8F6C\\u5316\""
  // i.e. the whole string is wrapped in quotes
  const trimmed = str.trim();
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    try {
      const parsed = JSON.parse(trimmed);
      if (typeof parsed === "string") {
        // Recursively normalize in case of triple-encoding
        return decodeUnicodeEscapes(parsed);
      }
    } catch {
      // Not valid JSON, fall through
    }
  }

  // Step 2: Try native JSON parse (handles standard \uXXXX within a string)
  try {
    const decoded = JSON.parse('"' + str.replace(/"/g, '\\"') + '"');
    if (typeof decoded === "string") return decoded;
  } catch {
    // Fallback: manual decode
  }

  // Step 3: Manual regex fallback
  return str.replace(/\\u([0-9A-Fa-f]{4})/g, (_, hex) =>
    String.fromCharCode(parseInt(hex, 16))
  );
}

/**
 * Safely stringify an object without crashing.
 */
function safeStringify(obj) {
  try {
    return JSON.stringify(obj, null, 0);
  } catch {
    return String(obj);
  }
}

/**
 * Normalize all string values in an object (shallow pass).
 * Useful for normalizing entire API response objects at the adapter boundary.
 */
export function normalizeObject(obj) {
  if (obj == null || typeof obj !== "object") return obj;
  if (Array.isArray(obj)) return obj.map(normalizeText);

  const result = {};
  for (const [key, value] of Object.entries(obj)) {
    result[key] = typeof value === "string" ? normalizeText(value) : value;
  }
  return result;
}

/**
 * Alias for backward compatibility.
 */
export const toSafeString = normalizeText;