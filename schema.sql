-- ============================================================
-- AI Code Review Assistant — Supabase schema
-- Run this in the Supabase SQL Editor (Database > SQL Editor)
-- ============================================================

-- Supabase already gives us auth.users (registration, login, logout,
-- password reset are handled by Supabase Auth — no custom users table
-- or password_hash column needed).
-- This "profiles" table just extends auth.users with app-specific fields.

create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  name text,
  created_at timestamptz not null default now()
);

-- Auto-create a profile row whenever a new user signs up via Supabase Auth
create function public.handle_new_user()
returns trigger as $$
begin
  insert into public.profiles (id, name)
  values (new.id, new.raw_user_meta_data->>'name');
  return new;
end;
$$ language plpgsql security definer;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();


-- ============================================================
-- Projects
-- ============================================================
create table public.projects (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  project_name text not null,
  upload_type text not null check (upload_type in ('file_upload', 'code_paste', 'github')),
  source_url text,                       -- populated only when upload_type = 'github'
  created_at timestamptz not null default now()
);

create index idx_projects_user_id on public.projects(user_id);


-- ============================================================
-- Reviews
-- ============================================================
create table public.reviews (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  status text not null default 'pending' check (status in ('pending', 'processing', 'completed', 'failed')),
  review_score numeric(5,2),             -- overall quality score, e.g. 0-100
  maintainability_index numeric(5,2),    -- from Radon
  cyclomatic_complexity integer,         -- from Radon
  total_lines_of_code integer,
  num_functions integer,
  num_classes integer,
  summary text,
  created_at timestamptz not null default now()
);

create index idx_reviews_project_id on public.reviews(project_id);
create index idx_reviews_status on public.reviews(status);


-- ============================================================
-- Review Findings
-- ============================================================
create table public.review_findings (
  id uuid primary key default gen_random_uuid(),
  review_id uuid not null references public.reviews(id) on delete cascade,
  severity text not null check (severity in ('low', 'medium', 'high', 'critical')),
  tool_source text not null check (tool_source in ('pylint', 'bandit', 'radon', 'ai')),
  issue text not null,
  explanation text,
  suggestion text,
  file_name text,
  line_number integer,
  created_at timestamptz not null default now()
);

create index idx_findings_review_id on public.review_findings(review_id);
create index idx_findings_severity on public.review_findings(severity);


-- ============================================================
-- Row Level Security (RLS)
-- Ensures users can only ever see/modify their own data.
-- Required in Supabase — without this, any authenticated user
-- could query any other user's rows via the API.
-- ============================================================

alter table public.profiles enable row level security;
alter table public.projects enable row level security;
alter table public.reviews enable row level security;
alter table public.review_findings enable row level security;

-- Profiles: users can only read/update their own profile
create policy "Users can view own profile"
  on public.profiles for select
  using (auth.uid() = id);

create policy "Users can update own profile"
  on public.profiles for update
  using (auth.uid() = id);

-- Projects: users can only manage their own projects
create policy "Users can view own projects"
  on public.projects for select
  using (auth.uid() = user_id);

create policy "Users can insert own projects"
  on public.projects for insert
  with check (auth.uid() = user_id);

create policy "Users can delete own projects"
  on public.projects for delete
  using (auth.uid() = user_id);

-- Reviews: access controlled via the parent project's ownership
create policy "Users can view own reviews"
  on public.reviews for select
  using (
    exists (
      select 1 from public.projects
      where projects.id = reviews.project_id
      and projects.user_id = auth.uid()
    )
  );

create policy "Users can insert reviews for own projects"
  on public.reviews for insert
  with check (
    exists (
      select 1 from public.projects
      where projects.id = reviews.project_id
      and projects.user_id = auth.uid()
    )
  );

create policy "Users can delete own reviews"
  on public.reviews for delete
  using (
    exists (
      select 1 from public.projects
      where projects.id = reviews.project_id
      and projects.user_id = auth.uid()
    )
  );

-- Review findings: access controlled via the review -> project chain
create policy "Users can view own review findings"
  on public.review_findings for select
  using (
    exists (
      select 1 from public.reviews
      join public.projects on projects.id = reviews.project_id
      where reviews.id = review_findings.review_id
      and projects.user_id = auth.uid()
    )
  );

create policy "Users can insert own review findings"
  on public.review_findings for insert
  with check (
    exists (
      select 1 from public.reviews
      join public.projects on projects.id = reviews.project_id
      where reviews.id = review_findings.review_id
      and projects.user_id = auth.uid()
    )
  );
