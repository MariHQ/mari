// DocReview moved to ./docreview/ (index, Editor, OutlinePanel, RefinePanel,
// ChangeQueue, FindingsPanel, markdown, data). This shim keeps App.tsx's
// `./pages/DocReview` import stable. Note the explicit "/index": the
// filesystem is case-insensitive, so "./docreview" would resolve back to this
// file. Plain import+export (not `export {default} from`) avoids a re-export
// alias cycle in Vite's dev-server module graph.
import DocReviewPage from "./docreview/index";

export default DocReviewPage;
