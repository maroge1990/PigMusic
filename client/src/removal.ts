/** What removing songs from your library would do, from the server. Its
 * confirmation is sent back with the removal, which then offers Undo. */
export type RemovalPreview = {
  confirmation: string;
  delete_count: number;
  keep_count: number;
  items: { track_id: string; delete_file: boolean; kept_for: string[] }[];
};
