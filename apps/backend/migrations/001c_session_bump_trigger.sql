-- Migration 001c: session message bump trigger.
-- Prerequisite: 001a (tables) already run.
--
-- Before:  append_chat_message() did 3 round-trips (insert + select + update)
--          and had a read-modify-write race.
-- After:   one insert; trigger bumps last_message_at / message_count /
--          tickers atomically.

CREATE OR REPLACE FUNCTION bump_chat_session()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  UPDATE chat_sessions
     SET last_message_at = NEW.created_at,
         message_count   = message_count + 1,
         tickers         = (
           SELECT ARRAY(SELECT DISTINCT t FROM unnest(tickers || NEW.tickers) AS t WHERE t IS NOT NULL)
         )
   WHERE id = NEW.session_id;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_bump_chat_session ON chat_messages;
CREATE TRIGGER trg_bump_chat_session
  AFTER INSERT ON chat_messages
  FOR EACH ROW
  EXECUTE FUNCTION bump_chat_session();
