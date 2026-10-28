import { Card, PageHeader } from "../components/ui";

export default function About() {
  return (
    <>
      <PageHeader title="About & Sources" subtitle="Where the data comes from, how metrics are defined, and what the numbers cannot tell you." />
      <div className="prose">
        <Card title="Data source">
          <p>
            All repository, issue and pull-request data comes from the public{" "}
            <a href="https://docs.github.com/en/rest" target="_blank" rel="noopener noreferrer">GitHub REST API</a>. This site is an
            independent portfolio project and is not affiliated with GitHub or with any of the listed projects. Repository names,
            issue titles and logins are public GitHub data and remain the property of their authors.
          </p>
        </Card>
        <Card title="How the data is collected">
          <ul>
            <li>A Python pipeline runs daily (and on demand) in GitHub Actions and writes to a hosted PostgreSQL database.</li>
            <li>
              The first run for a repository loads issues and pull requests <strong>updated in the previous 90 days</strong> plus{" "}
              <strong>every currently open</strong> issue and pull request, so old untouched items are included in backlog metrics.
              Later runs load only what changed, re-reading a 24-hour overlap.
            </li>
            <li>The issues endpoint also returns pull requests; those are excluded from issue metrics and loaded only from the pull-request endpoint, so nothing is counted twice.</li>
            <li>Every row is validated; invalid rows are quarantined with a reason, and every run records its quality checks.</li>
            <li>Stars and forks are captured once per day. History starts on the first real snapshot; nothing is back-filled or estimated.</li>
          </ul>
        </Card>
        <Card title="Metric definitions">
          <dl className="definitions">
            <dt>Open issues</dt><dd>Issues (never pull requests) whose current state is open.</dd>
            <dt>Backlog age</dt><dd>Days since an open issue was created, in UTC, at the time of the request.</dd>
            <dt>Issues closed per week</dt><dd>Issues whose final close date falls in the week (weeks start Monday, UTC).</dd>
            <dt>PR merge rate</dt><dd>Merged ÷ (merged + closed without merging), for pull requests closed in the selected window. Open PRs are excluded.</dd>
            <dt>Median time to merge</dt><dd>Median of merged_at − created_at for PRs merged in the selected window.</dd>
            <dt>Pipeline success rate</dt><dd>Runs with status “success” ÷ finished runs in the last 30 days. Partial runs do not count as successes.</dd>
            <dt>Coverage start</dt><dd>The first full UTC day from which opened/closed/merged events are complete for a repository. Weekly trends start there.</dd>
          </dl>
        </Card>
        <Card title="Limitations">
          <ul>
            <li>This is not a complete history of every past issue and pull-request event — only the coverage window plus currently open items.</li>
            <li>Issues and pull requests are stored in their current state; if an issue was closed, reopened and closed again, only the final close counts.</li>
            <li>Deleted, transferred or converted-to-discussion issues are not detected by incremental runs.</li>
            <li>Days on which the pipeline did not run appear as gaps in star/fork history.</li>
            <li>“Missing” is shown as “—”, never as zero. A failed or partial run is always labelled as such.</li>
          </ul>
        </Card>
        <Card title="Technology">
          <p>
            Python 3.12 · requests · pandas · Pandera · SQLAlchemy · Alembic · PostgreSQL (Neon) · FastAPI as a Vercel Function ·
            React, TypeScript, Vite and Recharts on Vercel · GitHub Actions for CI and scheduled ingestion.
          </p>
          <p>Source code is published under the MIT License.</p>
        </Card>
      </div>
    </>
  );
}
