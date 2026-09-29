export interface IndexReport {
  status: 'indexed' | 'partial' | 'error';
  total_chunks: number;
  indexed_chunks: number;
  failed_chunks: number;
  failures: { path: string; chunk_id: string; chunk_index: number; code: string; message: string }[];
  failures_truncated?: boolean;
  sanitized_data_uris?: number;
  reused_chunks?: number;
}

export const isSearchableIndex = (status: string | null | undefined) => status === 'indexed' || status === 'partial';
