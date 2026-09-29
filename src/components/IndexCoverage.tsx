import type { IndexReport } from '@/types/indexStatus';

export default function IndexCoverage({ report, error }: { report?: IndexReport | null; error?: string | null }) {
  if (!report && !error) return null;
  return <div className="mt-1 max-w-md space-y-1 text-xs text-[var(--muted)]">
    {report && <p>{report.indexed_chunks}/{report.total_chunks} chunks indexed · {report.failed_chunks} failed</p>}
    {error && <p className="break-words text-amber-700 dark:text-amber-400">{error}</p>}
    {!!report?.sanitized_data_uris && <p>{report.sanitized_data_uris} embedded data URI(s) removed</p>}
    {!!report?.failures?.length && <details>
      <summary className="cursor-pointer underline">Failure details · retry Reindex to reuse successful chunks</summary>
      <ul className="mt-2 max-h-48 space-y-2 overflow-y-auto">{report.failures.map((failure, index) => <li key={`${failure.chunk_id}-${index}`}>
        <code className="break-all">{failure.path}</code> · chunk {failure.chunk_index + 1}
        <p>{failure.message} ({failure.code})</p>
      </li>)}</ul>
      {report.failures_truncated && <p>Showing the first 100 failures; the index retains all failed chunks for retry.</p>}
    </details>}
  </div>;
}
