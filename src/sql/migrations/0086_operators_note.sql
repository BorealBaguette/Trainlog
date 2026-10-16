-- Free-text note for admins. Only the admin operators page reads it.
ALTER TABLE operators ADD COLUMN IF NOT EXISTS note TEXT;
