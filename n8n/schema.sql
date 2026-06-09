--
-- PostgreSQL database dump
--

\restrict GuW7BwRq3Xih7TcUOl0xLvyAJSddxo8NsL8siaUFVjBGfnbKwoZ9CvnAgqb2TZN

-- Dumped from database version 17.9 (Debian 17.9-1.pgdg13+1)
-- Dumped by pg_dump version 17.9 (Debian 17.9-1.pgdg13+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: article_interactions; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.article_interactions (
    id integer NOT NULL,
    user_id integer NOT NULL,
    article_id integer NOT NULL,
    actioned_at timestamp with time zone DEFAULT now(),
    status character varying(12) DEFAULT 'presented'::character varying NOT NULL,
    CONSTRAINT article_interactions_status_check CHECK (((status)::text = ANY (ARRAY['presented'::text, 'skipped'::text, 'read'::text, 'declined'::text])))
);


ALTER TABLE public.article_interactions OWNER TO postgres;

--
-- Name: article_interactions_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.article_interactions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.article_interactions_id_seq OWNER TO postgres;

--
-- Name: article_interactions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.article_interactions_id_seq OWNED BY public.article_interactions.id;


--
-- Name: article_user_scores; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.article_user_scores (
    id integer NOT NULL,
    article_id integer NOT NULL,
    user_id integer NOT NULL,
    ai_score integer,
    ai_reason text,
    category_id integer,
    ai_scored_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.article_user_scores OWNER TO postgres;

--
-- Name: article_user_scores_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.article_user_scores_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.article_user_scores_id_seq OWNER TO postgres;

--
-- Name: article_user_scores_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.article_user_scores_id_seq OWNED BY public.article_user_scores.id;


--
-- Name: articles; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.articles (
    id integer NOT NULL,
    title text,
    description text,
    url text NOT NULL,
    published_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    full_content text,
    content_fetched_at timestamp with time zone,
    voice_summary text,
    summarised_at timestamp with time zone,
    decay character varying(8),
    images jsonb DEFAULT '[]'::jsonb,
    CONSTRAINT articles_decay_check CHECK (((decay)::text = ANY ((ARRAY['fast'::character varying, 'moderate'::character varying, 'slow'::character varying])::text[])))
);


ALTER TABLE public.articles OWNER TO postgres;

--
-- Name: articles_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.articles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.articles_id_seq OWNER TO postgres;

--
-- Name: articles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.articles_id_seq OWNED BY public.articles.id;


--
-- Name: categories; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.categories (
    id integer NOT NULL,
    name text NOT NULL
);


ALTER TABLE public.categories OWNER TO postgres;

--
-- Name: categories_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.categories_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.categories_id_seq OWNER TO postgres;

--
-- Name: categories_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.categories_id_seq OWNED BY public.categories.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.users (
    id integer NOT NULL,
    name text NOT NULL,
    scoring_prompt text,
    created_at timestamp with time zone DEFAULT now(),
    borrows_scores_from integer
);


ALTER TABLE public.users OWNER TO postgres;

--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.users_id_seq OWNER TO postgres;

--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: article_interactions id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.article_interactions ALTER COLUMN id SET DEFAULT nextval('public.article_interactions_id_seq'::regclass);


--
-- Name: article_user_scores id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.article_user_scores ALTER COLUMN id SET DEFAULT nextval('public.article_user_scores_id_seq'::regclass);


--
-- Name: articles id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.articles ALTER COLUMN id SET DEFAULT nextval('public.articles_id_seq'::regclass);


--
-- Name: categories id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.categories ALTER COLUMN id SET DEFAULT nextval('public.categories_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: article_interactions article_interactions_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.article_interactions
    ADD CONSTRAINT article_interactions_pkey PRIMARY KEY (id);


--
-- Name: article_interactions article_interactions_user_id_article_id_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.article_interactions
    ADD CONSTRAINT article_interactions_user_id_article_id_key UNIQUE (user_id, article_id);


--
-- Name: article_user_scores article_user_scores_article_id_user_id_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.article_user_scores
    ADD CONSTRAINT article_user_scores_article_id_user_id_key UNIQUE (article_id, user_id);


--
-- Name: article_user_scores article_user_scores_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.article_user_scores
    ADD CONSTRAINT article_user_scores_pkey PRIMARY KEY (id);


--
-- Name: articles articles_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.articles
    ADD CONSTRAINT articles_pkey PRIMARY KEY (id);


--
-- Name: articles articles_url_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.articles
    ADD CONSTRAINT articles_url_key UNIQUE (url);


--
-- Name: categories categories_name_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.categories
    ADD CONSTRAINT categories_name_key UNIQUE (name);


--
-- Name: categories categories_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.categories
    ADD CONSTRAINT categories_pkey PRIMARY KEY (id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: article_interactions article_reads_article_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.article_interactions
    ADD CONSTRAINT article_reads_article_id_fkey FOREIGN KEY (article_id) REFERENCES public.articles(id) ON DELETE CASCADE;


--
-- Name: article_interactions article_reads_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.article_interactions
    ADD CONSTRAINT article_reads_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: article_user_scores article_user_scores_article_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.article_user_scores
    ADD CONSTRAINT article_user_scores_article_id_fkey FOREIGN KEY (article_id) REFERENCES public.articles(id) ON DELETE CASCADE;


--
-- Name: article_user_scores article_user_scores_category_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.article_user_scores
    ADD CONSTRAINT article_user_scores_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.categories(id);


--
-- Name: article_user_scores article_user_scores_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.article_user_scores
    ADD CONSTRAINT article_user_scores_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: users users_borrows_scores_from_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_borrows_scores_from_fkey FOREIGN KEY (borrows_scores_from) REFERENCES public.users(id);


--
-- PostgreSQL database dump complete
--

\unrestrict GuW7BwRq3Xih7TcUOl0xLvyAJSddxo8NsL8siaUFVjBGfnbKwoZ9CvnAgqb2TZN

