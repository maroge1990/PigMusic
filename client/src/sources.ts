/** Display names and one-line descriptions for download sources. */
export const sources: Record<string, { name: string; description: string }> = {
  soulseek: {
    name: "Soulseek",
    description: "Peer-to-peer, including lossless files.",
  },
  freemp3cloud: { name: "FreeMp3Cloud", description: "MP3 downloads." },
  zvu4no: { name: "zvu4no", description: "MP3 downloads." },
  jiosaavn: {
    name: "JioSaavn",
    description:
      "AAC 256 kbps; eligible for AAC and native audio; also used as the last-resort AAC fallback.",
  },
  soundcloud: {
    name: "SoundCloud",
    description: "Full recordings in the upload's supported native format.",
  },
  monochrome: {
    name: "Monochrome",
    description:
      "Verified FLAC recordings through the configured public endpoint.",
  },
};

export const sourceIds = Object.keys(sources);

// Removed sources keep their name in older download history (D-60).
const retired: Record<string, string> = { youtube: "YouTube" };

export const sourceName = (id: string) =>
  sources[id]?.name ?? retired[id] ?? id;
