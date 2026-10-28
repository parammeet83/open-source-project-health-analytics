import { lazy, Suspense } from "react";
import { Link, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Empty, Loading, PageHeader } from "./components/ui";
import Overview from "./pages/Overview";

const Explorer = lazy(() => import("./pages/Explorer"));
const RepositoryDetail = lazy(() => import("./pages/RepositoryDetail"));
const Issues = lazy(() => import("./pages/Issues"));
const PullRequests = lazy(() => import("./pages/PullRequests"));
const Quality = lazy(() => import("./pages/Quality"));
const About = lazy(() => import("./pages/About"));

function NotFound() {
  return (
    <>
      <PageHeader title="Page not found" />
      <Empty>
        That page does not exist. <Link to="/">Go to the overview</Link>.
      </Empty>
    </>
  );
}

export function AppRoutes() {
  return (
    <Layout>
      <Suspense fallback={<Loading label="Loading page…" />}>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/repositories" element={<Explorer />} />
          <Route path="/repositories/:repositoryId" element={<RepositoryDetail />} />
          <Route path="/issues" element={<Issues />} />
          <Route path="/pull-requests" element={<PullRequests />} />
          <Route path="/quality" element={<Quality />} />
          <Route path="/about" element={<About />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </Suspense>
    </Layout>
  );
}
