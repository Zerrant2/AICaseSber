"""КОНТРАКТ проекта «Сократ» (v1). Единственный источник правды о структурах данных
и интерфейсах между модулями.

Правила:
  * Импортируйте модели только отсюда: `from socrat.contracts import DiagnosticWork, ...`
  * Изменения контракта вносятся отдельным PR с меткой `contract`.
  * Добавлять НОВЫЕ необязательные поля (со значением по умолчанию) можно быстро;
    удалять/переименовывать — только с согласованием.
"""

from .analysis import (
    ANALYSIS_LIMITATIONS_DEFAULT,
    ErrorAnalysisRequest,
    ErrorAnalysisResult,
    ErrorHypothesis,
    QuickCheckTask,
    ReflectionObservation,
    ResponseObservation,
    TechniqueRecommendation,
    UUDObservationItem,
)
from .enums import (
    ERROR_LAYER_LABELS,
    LABELS_RU,
    STUDENT_VARIANT_LABEL,
    ChunkKind,
    DocType,
    EduLevel,
    ErrorLayer,
    GuardrailCode,
    Level,
    LevelChoice,
    OutcomeType,
    PresentationMode,
    Rating,
    Role,
    Severity,
    SubjectObservation,
    TaskKind,
    UUDFocus,
    UUDGroup,
    UUDObservation,
)
from .normative import (
    Chunk,
    KnowledgeStatus,
    Outcome,
    SearchHit,
    SourceDocument,
    Subject,
    TopicCheck,
    UpdateReport,
)
from .request import MAX_TASKS_DEFAULT, GenerationRequest, GuardrailIssue, GuardrailResult
from .services import (
    ErrorAnalyzer,
    Exporter,
    FeedbackRepository,
    GenerationError,
    KnowledgeBase,
    LLMClient,
    LLMResponse,
    LLMUsage,
    ProgressCallback,
    ResponseObserver,
    Services,
    TeacherRepository,
    UsageRepository,
    WorkGenerator,
    WorkRepository,
)
from .users import (
    Feedback,
    NewTeacherCredentials,
    SessionInfo,
    TeacherAccount,
    UsageRecord,
    UsageSummary,
)
from .views import StudentTask, StudentVariant, StudentView, student_view
from .work import (
    PERSONAL_NOTE,
    REFLECTION_NOTE,
    TIME_BASIS_NOTE,
    CoverageSummary,
    DiagnosticWork,
    GenerationMeta,
    OutcomeLink,
    Reflection,
    SolutionStep,
    Task,
    TaskChecks,
    TimePlan,
    TypicalError,
    UUDIndicator,
    Variant,
)

CONTRACT_VERSION = "1.0.0"

__all__ = [name for name in dir() if not name.startswith("_")]
