/// Response of `GET /api/v1/system/status`.
///
/// Flutter never talks to an LLM provider directly — FastAPI checks the
/// configured provider and returns only this summary.
class SystemStatusDto {
  /// Always `"online"` when this DTO exists (the response was received).
  final String backendStatus;

  /// `online` | `offline` | `unconfigured` | `unknown`
  final String llmStatus;

  /// `none` | `ollama` | `gemini` | `openai` | `anthropic`
  final String? llmProvider;
  final String? llmModel;
  final String? llmDetail;

  const SystemStatusDto({
    this.backendStatus = 'online',
    required this.llmStatus,
    this.llmProvider,
    this.llmModel,
    this.llmDetail,
  });

  /// Local-only fallback used when the backend is unreachable — the app must
  /// not pretend to know the LLM state.
  static const SystemStatusDto backendUnreachable = SystemStatusDto(
    backendStatus: 'offline',
    llmStatus: 'unknown',
  );

  factory SystemStatusDto.fromJson(Map<String, dynamic> json) {
    final backend = json['backend'] as Map<String, dynamic>? ?? const {};
    final llm = json['llm'] as Map<String, dynamic>? ?? const {};
    return SystemStatusDto(
      backendStatus: backend['status'] as String? ?? 'online',
      llmStatus: llm['status'] as String? ?? 'unknown',
      llmProvider: llm['provider'] as String?,
      llmModel: llm['model'] as String?,
      llmDetail: llm['detail'] as String?,
    );
  }

  bool get backendOnline => backendStatus == 'online';

  Map<String, dynamic> toJson() => {
        'backend': {'status': backendStatus},
        'llm': {
          'status': llmStatus,
          'provider': llmProvider,
          'model': llmModel,
          'detail': llmDetail,
        },
      };
}
