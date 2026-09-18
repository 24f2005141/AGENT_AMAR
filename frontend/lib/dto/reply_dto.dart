/// DTOs for the AI reply-suggestion + send feature.
///
/// The backend is the source of truth: it decides whether an email needs a
/// reply, generates exactly 3 options via the shared LLM abstraction, and sends
/// the final body verbatim from the authenticated user's Gmail. Flutter never
/// talks to an LLM or to Gmail directly.
class ReplySuggestionDto {
  final String id; // option_1 | option_2 | option_3
  final String label; // Direct | Professional | Alternative (backend-supplied)
  final String body;

  const ReplySuggestionDto({
    required this.id,
    required this.label,
    required this.body,
  });

  factory ReplySuggestionDto.fromJson(Map<String, dynamic> json) {
    return ReplySuggestionDto(
      id: json['id'] as String? ?? '',
      label: json['label'] as String? ?? '',
      body: json['body'] as String? ?? '',
    );
  }
}

class ReplySuggestionsDto {
  final String emailId;
  final List<ReplySuggestionDto> suggestions;

  const ReplySuggestionsDto({
    required this.emailId,
    required this.suggestions,
  });

  factory ReplySuggestionsDto.fromJson(Map<String, dynamic> json) {
    return ReplySuggestionsDto(
      emailId: json['email_id'] as String? ?? '',
      suggestions: (json['suggestions'] as List<dynamic>?)
              ?.map((e) => ReplySuggestionDto.fromJson(e as Map<String, dynamic>))
              .toList() ??
          const [],
    );
  }
}

class ReplySendResultDto {
  final String emailId;
  final String? threadId;
  final String? gmailMessageId;
  final bool replyActionCompleted;

  /// The email itself was marked done because the reply was sent.
  final bool emailMarkedCompleted;
  final bool duplicateSuppressed;

  const ReplySendResultDto({
    required this.emailId,
    this.threadId,
    this.gmailMessageId,
    this.replyActionCompleted = false,
    this.emailMarkedCompleted = false,
    this.duplicateSuppressed = false,
  });

  factory ReplySendResultDto.fromJson(Map<String, dynamic> json) {
    return ReplySendResultDto(
      emailId: json['email_id'] as String? ?? '',
      threadId: json['thread_id'] as String?,
      gmailMessageId: json['gmail_message_id'] as String?,
      replyActionCompleted: json['reply_action_completed'] as bool? ?? false,
      emailMarkedCompleted: json['email_marked_completed'] as bool? ?? false,
      duplicateSuppressed: json['duplicate_suppressed'] as bool? ?? false,
    );
  }
}
