-- Retire the plaintext MCP bearer column.
--
-- mcp_servers.token predates token_hash: the first releases stored the bearer
-- itself, and authenticate kept matching it (m.token = ...) so those servers
-- would not stop working when hashing arrived. That left a credential in the
-- clear in every dump and backup taken since. Hash whatever is still there
-- with the same digest the runtime compares (sha256, hex, UTF-8 bytes), then
-- blank the plaintext; the OR clause goes away with this release. A row that
-- already has a hash keeps it, since the runtime only ever wrote one bearer
-- per server and the hash is the one it issued. The column itself stays: the
-- baseline still declares it and the insert path writes '' into it.
UPDATE mcp_servers
   SET token_hash = encode(sha256(convert_to(token, 'UTF8')), 'hex')
 WHERE token IS NOT NULL AND token <> '' AND token_hash = '';
UPDATE mcp_servers SET token = '' WHERE token IS NOT NULL AND token <> '';
