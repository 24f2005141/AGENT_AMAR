import 'agent_analysis.dart';
import 'user_state.dart';

class Email {
  final String id;
  final String senderName;
  final String senderEmail;
  final String subject;
  final String body;
  final String snippet;
  final DateTime receivedAt;
  final bool isUnread;
  final List<String> labels;
  final AgentAnalysis analysis;
  final UserState userState;

  /// The single mutually-exclusive inbox bucket, decided by the backend
  /// (`primary_category`). Flutter filters sections on THIS, never on
  /// `analysis.actionRequired` / `priority` directly. It is the user's manual
  /// correction when they made one, else the automated derivation.
  final PrimaryCategory primaryCategory;

  /// The automated derivation, kept even after a user correction (Phase 18).
  final PrimaryCategory autoPrimaryCategory;

  /// True when the user manually corrected [primaryCategory].
  final bool primaryCategoryUserCorrected;

  const Email({
    required this.id,
    required this.senderName,
    required this.senderEmail,
    required this.subject,
    required this.body,
    required this.snippet,
    required this.receivedAt,
    this.isUnread = false,
    this.labels = const ['INBOX'],
    required this.analysis,
    this.userState = const UserState(),
    this.primaryCategory = PrimaryCategory.lowPriority,
    PrimaryCategory? autoPrimaryCategory,
    this.primaryCategoryUserCorrected = false,
  }) : autoPrimaryCategory = autoPrimaryCategory ?? primaryCategory;

  bool get isActionRequired => analysis.actionRequired && !userState.isCompleted;
  bool get hasDeadline => analysis.deadline != null;
  bool get isCritical => analysis.priority == PriorityLevel.critical;
  bool get isSnoozed => userState.isSnoozed;

  /// Whether this email still needs the user's attention — the rule the
  /// attention-dashboard homepage is built on. Mirrors the backend
  /// (`GET /api/v1/emails?active=true` / `EmailStateOut.is_active`):
  ///
  /// * resolved / completed, or currently snoozed → not active
  /// * Reply Required / Action Required → active until resolved (opening it is
  ///   not enough)
  /// * Important / Low Priority (nothing actionable) → active only until the
  ///   user opens / acknowledges it
  ///
  /// Used to drop an item from the feed the instant its state changes, without
  /// waiting for a refetch. The email is never deleted.
  bool get isActive {
    if (userState.isCompleted) return false;
    if (userState.isSnoozed) return false;
    if (primaryCategory == PrimaryCategory.replyRequired ||
        primaryCategory == PrimaryCategory.actionRequired) {
      return true;
    }
    return !userState.isViewed;
  }

  Email copyWith({
    String? id,
    String? senderName,
    String? senderEmail,
    String? subject,
    String? body,
    String? snippet,
    DateTime? receivedAt,
    bool? isUnread,
    List<String>? labels,
    AgentAnalysis? analysis,
    UserState? userState,
    PrimaryCategory? primaryCategory,
    PrimaryCategory? autoPrimaryCategory,
    bool? primaryCategoryUserCorrected,
  }) {
    return Email(
      id: id ?? this.id,
      senderName: senderName ?? this.senderName,
      senderEmail: senderEmail ?? this.senderEmail,
      subject: subject ?? this.subject,
      body: body ?? this.body,
      snippet: snippet ?? this.snippet,
      receivedAt: receivedAt ?? this.receivedAt,
      isUnread: isUnread ?? this.isUnread,
      labels: labels ?? this.labels,
      analysis: analysis ?? this.analysis,
      userState: userState ?? this.userState,
      primaryCategory: primaryCategory ?? this.primaryCategory,
      autoPrimaryCategory: autoPrimaryCategory ?? this.autoPrimaryCategory,
      primaryCategoryUserCorrected:
          primaryCategoryUserCorrected ?? this.primaryCategoryUserCorrected,
    );
  }

  factory Email.fromJson(Map<String, dynamic> json) {
    final senderObj = json['sender'];
    String sName = 'Unknown';
    String sEmail = '';
    if (senderObj is Map<String, dynamic>) {
      sName = senderObj['name'] ?? 'Unknown';
      sEmail = senderObj['email'] ?? '';
    } else if (json['sender_name'] != null) {
      sName = json['sender_name'];
      sEmail = json['sender_email'] ?? '';
    }

    return Email(
      id: json['id'] ?? json['email_id'] ?? '',
      senderName: sName,
      senderEmail: sEmail,
      subject: json['subject'] ?? '',
      body: json['body'] ?? json['snippet'] ?? '',
      snippet: json['snippet'] ?? '',
      primaryCategory: PrimaryCategory.fromWire(json['primary_category'] as String?),
      autoPrimaryCategory: PrimaryCategory.fromWire(
          (json['auto_primary_category'] ?? json['primary_category']) as String?),
      primaryCategoryUserCorrected:
          (json['primary_category_source'] as String?)?.toLowerCase() == 'user',
      receivedAt: json['received_at'] != null ? DateTime.parse(json['received_at']) : DateTime.now(),
      isUnread: json['is_unread'] ?? false,
      labels: (json['labels'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? ['INBOX'],
      analysis: json['analysis'] != null
          ? AgentAnalysis.fromJson(json['analysis'] as Map<String, dynamic>)
          : AgentAnalysis(
              category: 'GENERAL',
              priority: PriorityLevel.low,
              actionRequired: false,
              reasoningSummary: 'Standard communication.',
            ),
      userState: json['user_state'] != null
          ? UserState.fromJson(json['user_state'] as Map<String, dynamic>)
          : const UserState(),
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'sender_name': senderName,
    'sender_email': senderEmail,
    'subject': subject,
    'body': body,
    'snippet': snippet,
    'primary_category': primaryCategory.wire,
    'auto_primary_category': autoPrimaryCategory.wire,
    'primary_category_source': primaryCategoryUserCorrected ? 'user' : 'auto',
    'received_at': receivedAt.toIso8601String(),
    'is_unread': isUnread,
    'labels': labels,
    'analysis': analysis.toJson(),
    'user_state': userState.toJson(),
  };
}
