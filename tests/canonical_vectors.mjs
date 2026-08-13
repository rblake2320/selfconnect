import fs from "node:fs";

const vectors = JSON.parse(fs.readFileSync(new URL("./fixtures/seat_canonical_vectors.json", import.meta.url), "utf8"));
const MAX_SAFE = 9007199254740991n;

function numericTokens(raw) {
  const result = [];
  let inString = false;
  let escaped = false;
  for (let i = 0; i < raw.length; i += 1) {
    const char = raw[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === '"') inString = false;
      continue;
    }
    if (char === '"') {
      inString = true;
      continue;
    }
    if (char === "-" || (char >= "0" && char <= "9")) {
      const match = raw.slice(i).match(/^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/);
      if (!match) throw new Error("invalid number");
      result.push(match[0]);
      i += match[0].length - 1;
    }
  }
  return result;
}

function validateNumbers(raw) {
  for (const token of numericTokens(raw)) {
    if (token === "-0" || token.includes(".") || /[eE]/.test(token)) throw new Error("forbidden numeric form");
    const value = BigInt(token);
    if (value > MAX_SAFE || value < -MAX_SAFE) throw new Error("integer out of range");
  }
}

function rejectDuplicateNames(raw) {
  let index = 0;
  const whitespace = () => {
    while (/\s/.test(raw[index] ?? "")) index += 1;
  };
  const stringToken = () => {
    const start = index;
    if (raw[index++] !== '"') throw new Error("expected string");
    while (index < raw.length) {
      const char = raw[index++];
      if (char === '"') return JSON.parse(raw.slice(start, index));
      if (char === "\\") {
        const escaped = raw[index++];
        if (escaped === "u") {
          if (!/^[0-9a-fA-F]{4}$/.test(raw.slice(index, index + 4))) throw new Error("invalid escape");
          index += 4;
        } else if (!'"\\/bfnrt'.includes(escaped)) throw new Error("invalid escape");
      } else if (char.charCodeAt(0) < 0x20) throw new Error("invalid string control");
    }
    throw new Error("unterminated string");
  };
  const value = () => {
    whitespace();
    if (raw[index] === "{") {
      index += 1;
      whitespace();
      const names = new Set();
      if (raw[index] === "}") { index += 1; return; }
      while (true) {
        whitespace();
        const name = stringToken();
        if (names.has(name)) throw new Error(`duplicate JSON name: ${name}`);
        names.add(name);
        whitespace();
        if (raw[index++] !== ":") throw new Error("expected colon");
        value();
        whitespace();
        const delimiter = raw[index++];
        if (delimiter === "}") return;
        if (delimiter !== ",") throw new Error("expected object delimiter");
      }
    }
    if (raw[index] === "[") {
      index += 1;
      whitespace();
      if (raw[index] === "]") { index += 1; return; }
      while (true) {
        value();
        whitespace();
        const delimiter = raw[index++];
        if (delimiter === "]") return;
        if (delimiter !== ",") throw new Error("expected array delimiter");
      }
    }
    if (raw[index] === '"') { stringToken(); return; }
    const match = raw.slice(index).match(/^(?:true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)/);
    if (!match) throw new Error("invalid JSON value");
    index += match[0].length;
  };
  value();
  whitespace();
  if (index !== raw.length) throw new Error("trailing JSON data");
}

function quote(value) {
  let output = '"';
  for (let i = 0; i < value.length; i += 1) {
    const code = value.charCodeAt(i);
    if (code >= 0xd800 && code <= 0xdbff) {
      const low = value.charCodeAt(i + 1);
      if (!(low >= 0xdc00 && low <= 0xdfff)) throw new Error("lone surrogate");
      output += `\\u${code.toString(16).padStart(4, "0")}\\u${low.toString(16).padStart(4, "0")}`;
      i += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      throw new Error("lone surrogate");
    } else if (code < 0x20 || code > 0x7e || code === 0x22 || code === 0x5c) {
      const escapes = {8: "\\b", 9: "\\t", 10: "\\n", 12: "\\f", 13: "\\r", 34: '\\"', 92: "\\\\"};
      output += escapes[code] ?? `\\u${code.toString(16).padStart(4, "0")}`;
    } else output += value[i];
  }
  return `${output}"`;
}

function canonical(value) {
  if (value === null || typeof value === "boolean" || typeof value === "number") return JSON.stringify(value);
  if (typeof value === "string") return quote(value.normalize("NFC"));
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  const normalized = new Map();
  for (const [key, item] of Object.entries(value)) {
    const nfc = key.normalize("NFC");
    if ([...nfc].some(char => char.codePointAt(0) < 0x20 || char.codePointAt(0) > 0x7e)) throw new Error("non-ASCII object key");
    if (normalized.has(nfc)) throw new Error("duplicate normalized key");
    normalized.set(nfc, item);
  }
  return `{${[...normalized.keys()].sort().map(key => `${quote(key)}:${canonical(normalized.get(key))}`).join(",")}}`;
}

function parseCanonical(raw) {
  rejectDuplicateNames(raw);
  validateNumbers(raw);
  return canonical(JSON.parse(raw));
}

for (const vector of vectors.valid) {
  const actual = parseCanonical(vector.input);
  if (actual !== vector.canonical) throw new Error(`${vector.name}: ${actual} != ${vector.canonical}`);
}
for (const vector of vectors.invalid) {
  let rejected = false;
  try { parseCanonical(vector.input); } catch { rejected = true; }
  if (!rejected) throw new Error(`${vector.name}: expected rejection`);
}
console.log(`canonical vectors passed: ${vectors.valid.length} valid, ${vectors.invalid.length} invalid`);
