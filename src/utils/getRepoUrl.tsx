import RepoInfo from "@/types/repoinfo";

export default function getRepoUrl(repoInfo: RepoInfo): string {
  if (repoInfo.type === 'local' && repoInfo.localPath) {
    return repoInfo.localPath;
  }
  if (repoInfo.repoUrl) {
    return repoInfo.repoUrl;
  }
  // Construct a GitHub URL as best-effort fallback — this only applies
  // when the repo was opened without an explicit repo_url query param
  // (i.e. public GitHub repos accessed via /{owner}/{repo}).
  if (repoInfo.owner && repoInfo.repo) {
    const domain =
      repoInfo.type === 'gitlab' ? 'https://gitlab.com' :
      repoInfo.type === 'bitbucket' ? 'https://bitbucket.org' :
      'https://github.com';
    return `${domain}/${repoInfo.owner}/${repoInfo.repo}`;
  }
  return '';
}