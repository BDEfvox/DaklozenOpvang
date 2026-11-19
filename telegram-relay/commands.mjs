// "/Start@my_bot  abc123 more" -> { command: "start", arg: "abc123" }   (the command is case-insensitive,
// the argument is not: it is the invite code). Text that is not a command gives { command: "", arg: "" }.
export function parseCommand(text) {
  const t = typeof text === "string" ? text.trim() : "";
  if (!t.startsWith("/")) return { command: "", arg: "" };
  const parts = t.split(/\s+/);
  return { command: parts[0].split("@")[0].slice(1).toLowerCase(), arg: parts[1] ?? "" };
}
