// Publish moved to ./publish/ (index, SiteEditor, McpServersTab, data).
// This shim keeps App.tsx's `./pages/Publish` import stable. Note the explicit
// "/index": the filesystem is case-insensitive, so "./publish" would resolve
// back to this file. Plain import+export (not `export {default} from`) avoids
// a re-export alias cycle in Vite's dev-server module graph.
import PublishPage from "./publish/index";

export default PublishPage;
