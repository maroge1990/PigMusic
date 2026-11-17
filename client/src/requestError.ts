const sources: Record<string, string> = {
  soulseek: "Soulseek",
  freemp3cloud: "FreeMp3Cloud",
  zvu4no: "Zvu4no",
  jiosaavn: "JioSaavn",
};
const noMatch = "no candidate passed recording/version matching";

// Turns the server's technical failure text into a short sentence for members.
export function friendlyRequestError(error: string): string {
  const parts = error.split("; ").map((part) => {
    const split = part.indexOf(": ");
    const key = split > 0 ? part.slice(0, split).toLowerCase() : "";
    return key in sources
      ? { name: sources[key], message: part.slice(split + 2) }
      : null;
  });
  if (!parts.length || parts.some((p) => !p)) return error;
  const found = parts as { name: string; message: string }[];
  if (found.every((p) => p.message === noMatch))
    return "No source had a matching copy of this song.";
  const unreachable = found.filter((p) =>
    /search unavailable|transfer failed/.test(p.message),
  );
  if (unreachable.length) {
    const names = [...new Set(unreachable.map((p) => p.name))].join(", ");
    return `${names} couldn't be reached or couldn't send the song. Try again later.`;
  }
  return error;
}
