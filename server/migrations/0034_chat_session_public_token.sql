-- Public knowledge-chat sessions have no owner, and "no owner" used to be the
-- whole test for continuing one: a visitor who guessed the sequential id of
-- another visitor's session could append to it and have its history fed to
-- the model. Each new public session now carries an unguessable token that the
-- widget echoes back with the id. Rows from before this column keep NULL and
-- cannot be continued; a widget still holding one of those ids starts afresh.
ALTER TABLE chat_sessions ADD COLUMN public_token text;
