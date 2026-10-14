-- The product a ticket is (Interrail, Deutschlandticket): an entry of the 'ticket' pool of
-- operators, for its logo and spellings, never offered for a trip. Tickets of one product group
-- together whatever their names. NULL is found from the name, read by read; '' is none, the
-- user having taken off the one found.
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS product TEXT;
