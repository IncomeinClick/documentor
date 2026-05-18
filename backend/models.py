import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, Text, Integer, DateTime, ForeignKey
from backend.database import Base


def new_id():
    return str(uuid.uuid4())


def utcnow():
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"
    id = Column(Text, primary_key=True, default=new_id)
    name = Column(Text, nullable=False)
    slug = Column(Text, unique=True, nullable=False)
    credential_id = Column(Text, nullable=True)
    language = Column(Text, default="en")  # en, th, tl, id, etc.
    platforms = Column(Text, default="fb")  # comma-separated: fb, ig, fb,ig
    image_settings = Column(Text, nullable=True)  # JSON
    created_at = Column(DateTime, default=utcnow)


class Credential(Base):
    __tablename__ = "credentials"
    id = Column(Text, primary_key=True)  # user-defined slug
    name = Column(Text, nullable=False)
    page_id = Column(Text, nullable=False)
    access_token = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow)


class Content(Base):
    __tablename__ = "contents"
    id = Column(Text, primary_key=True, default=new_id)
    project_id = Column(Text, ForeignKey("projects.id"), nullable=False)
    title = Column(Text, nullable=False)
    caption = Column(Text, nullable=True)  # caption text posted with the headline image
    status = Column(Text, default="draft")  # draft/executing/posted/posted_draft/failed
    image_method = Column(Text, default="text_on_bg")  # text_on_bg | infographic_ai
    scheduled_at = Column(DateTime, nullable=True)  # legacy; scheduling removed but column kept for old rows
    source = Column(Text, default="manual")
    fb_post_id = Column(Text, nullable=True)
    ig_post_id = Column(Text, nullable=True)
    posted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class Block(Base):
    __tablename__ = "blocks"
    id = Column(Text, primary_key=True, default=new_id)
    content_id = Column(Text, ForeignKey("contents.id", ondelete="CASCADE"), nullable=False)
    sort_order = Column(Integer, nullable=False)  # 0=headline, 1+=comments
    text = Column(Text, nullable=False)
    image_path = Column(Text, nullable=True)
    image_url = Column(Text, nullable=True)
    fb_comment_id = Column(Text, nullable=True)
    ig_comment_id = Column(Text, nullable=True)
    status = Column(Text, default="pending")  # pending/posted/failed
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class Idea(Base):
    __tablename__ = "ideas"
    id = Column(Text, primary_key=True, default=new_id)
    research_date = Column(Text, nullable=False, index=True)  # YYYY-MM-DD Thai
    category = Column(Text, nullable=False)  # news/oss/case_study/how_to/youtube/failure
    sort_order = Column(Integer, nullable=False, default=0)  # within category (news=3 items)
    title = Column(Text, nullable=False)
    url = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    why_stand_out = Column(Text, nullable=True)
    confidence = Column(Text, nullable=True)
    posted_to_en_at = Column(DateTime, nullable=True, index=True)  # set when inc-content-en cron publishes EN version
    posted_to_th_at = Column(DateTime, nullable=True, index=True)  # set when inc-content-th cron publishes TH version
    created_at = Column(DateTime, default=utcnow)
