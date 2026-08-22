-- The `chat` style pack: how Mari writes an answer in the dock, in a public
-- knowledge chat, and in Slack. Source of truth for the prose is
-- docs/chat-style.md; the rules below are its compact form and are kept in step
-- with CHAT_STYLE_RULES in mari_server/conversations/prompts.py, which is the
-- fallback for a workspace with no pack.
--
-- Shipped as builtin so the Library shows it beside the four prose packs, but
-- it is a normal pack: a workspace can edit these rules, and
-- settings.style_guide.chat_pack can point the assistant at a different one.

INSERT INTO style_guides (key, name, description, tone, builtin, sort) VALUES
  ('chat', 'Chat answers',
   'How the assistant answers: lead with the answer, cite every claim, say plainly when the sources do not cover it.',
   'info', true, 5)
ON CONFLICT (key) DO NOTHING;

INSERT INTO style_rules (id, guide_key, family, severity, description, pack, suggestion, sort) VALUES
  ('chat.lead',      'chat', 'Chat', 'error',    'Lead with the answer in the first sentence. No preamble and no restating the question.',                                          'chat-01', 'Move the answer to sentence one.',                    10),
  ('chat.plain',     'chat', 'Chat', 'warn',     'Write plainly and directly: short sentences, active voice, sentence case.',                                                        'chat-02', 'Cut the modifiers and name the actor.',               20),
  ('chat.paragraph', 'chat', 'Chat', 'advisory', 'Keep paragraphs to three sentences or fewer.',                                                                                     'chat-03', 'Split the paragraph.',                                30),
  ('chat.structure', 'chat', 'Chat', 'advisory', 'Use a list only for ordered steps or a real enumeration, and a table only when comparing things across the same attributes.',      'chat-04', 'Write it as prose instead.',                          40),
  ('chat.cite',      'chat', 'Chat', 'error',    'Cite every factual claim drawn from the context, placing [n] right after the claim it supports and matching the numbers you were given.', 'chat-05', 'Add the citation after the claim.',              50),
  ('chat.no-answer', 'chat', 'Chat', 'error',    'When the context does not cover the question, say "I could not find this in the connected sources" and stop. Do not guess and do not hedge.', 'chat-06', 'Say what is missing and stop.',              60),
  ('chat.no-invent', 'chat', 'Chat', 'error',    'Never invent an id, a URL, a document, or a person''s name.',                                                                      'chat-07', 'Use only ids that appear in the context.',            70),
  ('chat.no-hype',   'chat', 'Chat', 'warn',     'No marketing voice, no exclamation marks, no em dashes or en dashes.',                                                             'chat-08', 'State the fact without the adjective.',               80),
  ('chat.length',    'chat', 'Chat', 'advisory', 'Answer a lookup in one to three sentences, a how-to as numbered steps, and a comparison as a table or a short list.',              'chat-09', 'Match the shape to the question.',                    90),
  ('chat.code',      'chat', 'Chat', 'advisory', 'Put code in a fenced block with a language tag.',                                                                                  'chat-10', 'Fence the block and name the language.',             100)
ON CONFLICT (id) DO NOTHING;

-- settings.style_guide.chat_pack names the pack the assistant speaks in. It is
-- deliberately separate from default_pack: default_pack governs prose the team
-- writes, and adopting "AI slop" as a document checker must not silently
-- rewrite how the assistant talks.
UPDATE settings
   SET value = value || '{"chat_pack":"chat"}'::jsonb
 WHERE key = 'style_guide' AND value->>'chat_pack' IS NULL;
