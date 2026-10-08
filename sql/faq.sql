-- FAQ-Tabelle: Website (/Infos, per faq_sync.py sync), intern (vormals faqs/allgemein.md)
-- und land (vormals faqs/FAQ_*.csv, kategorie = Land).
-- Einmal per Hand im Supabase-SQL-Editor ausführen (der API-Client kann kein DDL).

create table faq (
  id          bigint generated always as identity primary key,
  quelle      text not null check (quelle in ('website', 'intern', 'land')),
  extern_id   text unique,          -- tos-txtnr der Website, z.B. 'CHA-T-FAQ-55'; null bei intern
  kategorie   text not null,
  frage       text not null,
  antwort     text not null,        -- Markdown, ohne "A: "
  position    int  not null,        -- Reihenfolge im Prompt: intern 0.., website 1000.., land 2000..
  aktiv       boolean not null default true,   -- vom Sync gepflegt (auf /Infos vorhanden?)
  ausgeblendet boolean not null default false, -- vom Owner gesetzt, z.B. bei Überschneidung; der Sync fasst es nie an
  updated_at  timestamptz not null default now()
);

-- Historie: jede Änderung und Löschung, auch von Hand im Table Editor.
create table faq_history (
  id           bigint generated always as identity primary key,
  faq_id       bigint not null,
  operation    text not null,       -- 'UPDATE' | 'DELETE'
  alt          jsonb not null,      -- die Zeile vor der Änderung
  geaendert_am timestamptz not null default now()
);

create function faq_vor_aenderung() returns trigger language plpgsql as $$
begin
  insert into faq_history (faq_id, operation, alt) values (old.id, tg_op, to_jsonb(old));
  if tg_op = 'UPDATE' then
    new.updated_at := now();
    return new;
  end if;
  return old;
end $$;

create trigger faq_historie before update or delete on faq
  for each row execute function faq_vor_aenderung();

-- Nur Service-Role, keine öffentlichen Policies (wie sitemap_versions).
alter table faq enable row level security;
alter table faq_history enable row level security;

-- Nachtrag 2026-10-08 (Länder-FAQs), für die schon angelegte Tabelle:
-- alter table faq drop constraint faq_quelle_check;
-- alter table faq add constraint faq_quelle_check check (quelle in ('website', 'intern', 'land'));
-- Name prüfen, falls das drop scheitert: select conname from pg_constraint where conrelid = 'faq'::regclass;
