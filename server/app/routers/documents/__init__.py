"""documents 统一文档 API：CRUD、上传（后端元数据提取）、Range 流式取文件、封面、标签、双链同步、反链。

拆分后的包结构：
- filters: 列表/计数共用过滤条件
- crud: CRUD、列表、重索引
- upload: 上传与后台 OCR 分类
- files: 原始文件 Range 流式下载与封面
- stats: 书架统计与计数
- tags: 标签设置
- versions: 版本历史
- metadata: 在线元数据
- insight: 反链、未链接提及、关键词/摘要
"""

from ...services.tags import get_or_create_tag
from ...services.text_analysis import _context_snippet, extract_keywords, one_line_summary
from ._shared import (
    MENTION_PATTERN,
    get_doc_or_404,
    router,
    settings,
)

# 先注册 /stats、/count 等单段静态路由，避免被 crud 的 /{doc_id} 抢先匹配
from . import stats  # noqa: E402  isort: skip
from . import crud, files, insight, metadata, tags, upload, versions  # noqa: E402  isort: skip

from .crud import (  # noqa: E402
    CONTENT_PREVIEW_FETCH_CHARS,
    LIST_DEFAULT_LIMIT,
    LIST_HARD_LIMIT,
    SUMMARY_PREVIEW_CHARS,
    create_document,
    delete_document,
    get_document,
    list_documents,
    reindex_document_content,
    reindex_status,
    summarize_content,
    update_document,
)
from .files import (  # noqa: E402
    FILE_CHUNK_SIZE,
    MIME_BY_FORMAT,
    RANGE_PATTERN,
    _iter_file_range,
    file_download_headers,
    get_document_cover,
    get_document_file,
)
from .filters import (  # noqa: E402
    apply_document_filters,
    apply_status_filter,
    apply_types_filter,
)
from .insight import (  # noqa: E402
    Backlink,
    DocumentInsight,
    get_backlinks,
    get_document_insight,
    get_unlinked_mentions,
    sync_mention_links,
)
from .metadata import (  # noqa: E402
    MetadataApply,
    MetadataCandidate,
    apply_metadata_online,
    search_metadata_online,
)
from .stats import (  # noqa: E402
    BOOK_TYPES,
    CountOut,
    LibraryStats,
    count_documents,
    library_stats,
)
from .tags import set_document_tags  # noqa: E402
from .upload import (  # noqa: E402
    UPLOAD_FORMAT_WHITELIST,
    classify_ocr_background,
    upload_document,
)
from .versions import (  # noqa: E402
    VERSION_MAX_PER_DOC,
    VERSION_MIN_INTERVAL_SECONDS,
    _snapshot_version,
    get_document_version,
    list_document_versions,
    restore_document_version,
)

# 兼容旧私有导入；新代码请直接 from app.services.tags import get_or_create_tag
_get_or_create_tag = get_or_create_tag
