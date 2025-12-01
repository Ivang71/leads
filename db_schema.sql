create table if not exists users (
    id bigserial primary key,
    uid text not null unique,
    telegram_id bigint not null unique,
    telegram_username text,
    display_name text,
    first_name text,
    last_name text,
    locale text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists subscriptions (
    id bigserial primary key,
    user_id bigint not null references users(id) on delete cascade,
    plan_code text not null,
    status text not null,
    started_at timestamptz not null,
    expires_at timestamptz,
    canceled_at timestamptz,
    usage_quota integer,
    usage_used integer not null default 0,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists subscriptions_one_active_per_user
    on subscriptions(user_id)
    where status = 'active';

create table if not exists conversations (
    id bigserial primary key,
    user_id bigint not null references users(id) on delete cascade,
    started_at timestamptz not null default now(),
    ended_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists messages (
    id bigserial primary key,
    user_id bigint not null references users(id) on delete cascade,
    conversation_id bigint references conversations(id) on delete set null,
    role text not null,
    content text not null,
    created_at timestamptz not null default now()
);


