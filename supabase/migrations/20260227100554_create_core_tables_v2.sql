/*
  # Create core application tables (safe idempotent version)

  Creates profiles, folders, pdfs, links, domains, platform_settings tables
  with RLS policies and a trigger to auto-create profiles on signup.
  All statements use IF NOT EXISTS and DO blocks to be safe.
*/

CREATE TABLE IF NOT EXISTS profiles (
  id uuid PRIMARY KEY REFERENCES auth.users ON DELETE CASCADE,
  name text NOT NULL DEFAULT '',
  email text NOT NULL DEFAULT '',
  role text NOT NULL DEFAULT 'user',
  subscription_status text NOT NULL DEFAULT 'inactive',
  plan text NOT NULL DEFAULT 'none',
  storage_used bigint NOT NULL DEFAULT 0,
  language text NOT NULL DEFAULT 'en',
  picture text DEFAULT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='profiles' AND policyname='Users can view own profile') THEN
    CREATE POLICY "Users can view own profile" ON profiles FOR SELECT TO authenticated USING (auth.uid() = id);
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='profiles' AND policyname='Users can update own profile') THEN
    CREATE POLICY "Users can update own profile" ON profiles FOR UPDATE TO authenticated USING (auth.uid() = id) WITH CHECK (auth.uid() = id);
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='profiles' AND policyname='Admins can view all profiles') THEN
    CREATE POLICY "Admins can view all profiles" ON profiles FOR SELECT TO authenticated
      USING (EXISTS (SELECT 1 FROM profiles p WHERE p.id = auth.uid() AND p.role = 'admin'));
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='profiles' AND policyname='Admins can update all profiles') THEN
    CREATE POLICY "Admins can update all profiles" ON profiles FOR UPDATE TO authenticated
      USING (EXISTS (SELECT 1 FROM profiles p WHERE p.id = auth.uid() AND p.role = 'admin'))
      WITH CHECK (EXISTS (SELECT 1 FROM profiles p WHERE p.id = auth.uid() AND p.role = 'admin'));
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='profiles' AND policyname='Admins can delete profiles') THEN
    CREATE POLICY "Admins can delete profiles" ON profiles FOR DELETE TO authenticated
      USING (EXISTS (SELECT 1 FROM profiles p WHERE p.id = auth.uid() AND p.role = 'admin'));
  END IF;
END $$;

CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER SET search_path = public
AS $$
BEGIN
  INSERT INTO profiles (id, name, email, language)
  VALUES (
    new.id,
    COALESCE(new.raw_user_meta_data->>'name', ''),
    COALESCE(new.email, ''),
    COALESCE(new.raw_user_meta_data->>'language', 'en')
  )
  ON CONFLICT (id) DO NOTHING;
  RETURN new;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE PROCEDURE handle_new_user();


CREATE TABLE IF NOT EXISTS folders (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE folders ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='folders' AND policyname='Users can view own folders') THEN
    CREATE POLICY "Users can view own folders" ON folders FOR SELECT TO authenticated USING (user_id = auth.uid());
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='folders' AND policyname='Users can create own folders') THEN
    CREATE POLICY "Users can create own folders" ON folders FOR INSERT TO authenticated WITH CHECK (user_id = auth.uid());
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='folders' AND policyname='Users can update own folders') THEN
    CREATE POLICY "Users can update own folders" ON folders FOR UPDATE TO authenticated USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='folders' AND policyname='Users can delete own folders') THEN
    CREATE POLICY "Users can delete own folders" ON folders FOR DELETE TO authenticated USING (user_id = auth.uid());
  END IF;
END $$;


CREATE TABLE IF NOT EXISTS pdfs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  folder_id uuid REFERENCES folders(id) ON DELETE SET NULL,
  filename text NOT NULL,
  original_filename text NOT NULL DEFAULT '',
  storage_path text NOT NULL,
  file_size bigint NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE pdfs ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='pdfs' AND policyname='Users can view own PDFs') THEN
    CREATE POLICY "Users can view own PDFs" ON pdfs FOR SELECT TO authenticated USING (user_id = auth.uid());
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='pdfs' AND policyname='Users can insert own PDFs') THEN
    CREATE POLICY "Users can insert own PDFs" ON pdfs FOR INSERT TO authenticated WITH CHECK (user_id = auth.uid());
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='pdfs' AND policyname='Users can update own PDFs') THEN
    CREATE POLICY "Users can update own PDFs" ON pdfs FOR UPDATE TO authenticated USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='pdfs' AND policyname='Users can delete own PDFs') THEN
    CREATE POLICY "Users can delete own PDFs" ON pdfs FOR DELETE TO authenticated USING (user_id = auth.uid());
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='pdfs' AND policyname='Admins can view all PDFs') THEN
    CREATE POLICY "Admins can view all PDFs" ON pdfs FOR SELECT TO authenticated
      USING (EXISTS (SELECT 1 FROM profiles p WHERE p.id = auth.uid() AND p.role = 'admin'));
  END IF;
END $$;


CREATE TABLE IF NOT EXISTS links (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  pdf_id uuid NOT NULL REFERENCES pdfs(id) ON DELETE CASCADE,
  token text UNIQUE NOT NULL,
  status text NOT NULL DEFAULT 'active',
  expiry_mode text NOT NULL DEFAULT 'manual',
  expiry_duration_seconds int DEFAULT NULL,
  expiry_fixed_datetime timestamptz DEFAULT NULL,
  expires_at timestamptz DEFAULT NULL,
  open_count int NOT NULL DEFAULT 0,
  first_open_at timestamptz DEFAULT NULL,
  ip_sessions jsonb NOT NULL DEFAULT '{}',
  unique_ips text[] NOT NULL DEFAULT '{}',
  custom_expired_url text DEFAULT NULL,
  custom_expired_message text DEFAULT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE links ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='links' AND policyname='Users can view own links') THEN
    CREATE POLICY "Users can view own links" ON links FOR SELECT TO authenticated USING (user_id = auth.uid());
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='links' AND policyname='Users can create own links') THEN
    CREATE POLICY "Users can create own links" ON links FOR INSERT TO authenticated WITH CHECK (user_id = auth.uid());
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='links' AND policyname='Users can update own links') THEN
    CREATE POLICY "Users can update own links" ON links FOR UPDATE TO authenticated USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='links' AND policyname='Users can delete own links') THEN
    CREATE POLICY "Users can delete own links" ON links FOR DELETE TO authenticated USING (user_id = auth.uid());
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='links' AND policyname='Admins can view all links') THEN
    CREATE POLICY "Admins can view all links" ON links FOR SELECT TO authenticated
      USING (EXISTS (SELECT 1 FROM profiles p WHERE p.id = auth.uid() AND p.role = 'admin'));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='links' AND policyname='Admins can update all links') THEN
    CREATE POLICY "Admins can update all links" ON links FOR UPDATE TO authenticated
      USING (EXISTS (SELECT 1 FROM profiles p WHERE p.id = auth.uid() AND p.role = 'admin'))
      WITH CHECK (EXISTS (SELECT 1 FROM profiles p WHERE p.id = auth.uid() AND p.role = 'admin'));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='links' AND policyname='Admins can delete all links') THEN
    CREATE POLICY "Admins can delete all links" ON links FOR DELETE TO authenticated
      USING (EXISTS (SELECT 1 FROM profiles p WHERE p.id = auth.uid() AND p.role = 'admin'));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='links' AND policyname='Anyone can read links by token for viewing') THEN
    CREATE POLICY "Anyone can read links by token for viewing" ON links FOR SELECT TO anon USING (true);
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='links' AND policyname='Anon can update link view counts') THEN
    CREATE POLICY "Anon can update link view counts" ON links FOR UPDATE TO anon USING (true) WITH CHECK (true);
  END IF;
END $$;


CREATE TABLE IF NOT EXISTS domains (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  domain text UNIQUE NOT NULL,
  verified boolean NOT NULL DEFAULT false,
  verification_token text,
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE domains ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='domains' AND policyname='Users can view own domains') THEN
    CREATE POLICY "Users can view own domains" ON domains FOR SELECT TO authenticated USING (user_id = auth.uid());
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='domains' AND policyname='Users can create own domains') THEN
    CREATE POLICY "Users can create own domains" ON domains FOR INSERT TO authenticated WITH CHECK (user_id = auth.uid());
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='domains' AND policyname='Users can delete own domains') THEN
    CREATE POLICY "Users can delete own domains" ON domains FOR DELETE TO authenticated USING (user_id = auth.uid());
  END IF;
END $$;


CREATE TABLE IF NOT EXISTS platform_settings (
  key text PRIMARY KEY,
  value jsonb NOT NULL DEFAULT '{}',
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE platform_settings ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='platform_settings' AND policyname='Admins can view platform settings') THEN
    CREATE POLICY "Admins can view platform settings" ON platform_settings FOR SELECT TO authenticated
      USING (EXISTS (SELECT 1 FROM profiles p WHERE p.id = auth.uid() AND p.role = 'admin'));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='platform_settings' AND policyname='Admins can insert platform settings') THEN
    CREATE POLICY "Admins can insert platform settings" ON platform_settings FOR INSERT TO authenticated
      WITH CHECK (EXISTS (SELECT 1 FROM profiles p WHERE p.id = auth.uid() AND p.role = 'admin'));
  END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='platform_settings' AND policyname='Admins can update platform settings') THEN
    CREATE POLICY "Admins can update platform settings" ON platform_settings FOR UPDATE TO authenticated
      USING (EXISTS (SELECT 1 FROM profiles p WHERE p.id = auth.uid() AND p.role = 'admin'))
      WITH CHECK (EXISTS (SELECT 1 FROM profiles p WHERE p.id = auth.uid() AND p.role = 'admin'));
  END IF;
END $$;


CREATE INDEX IF NOT EXISTS idx_pdfs_user_id ON pdfs(user_id);
CREATE INDEX IF NOT EXISTS idx_links_user_id ON links(user_id);
CREATE INDEX IF NOT EXISTS idx_links_token ON links(token);
CREATE INDEX IF NOT EXISTS idx_links_pdf_id ON links(pdf_id);
CREATE INDEX IF NOT EXISTS idx_folders_user_id ON folders(user_id);
