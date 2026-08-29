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
  });

  bool get isActionRequired => analysis.actionRequired && !userState.isCompleted;
  bool get hasDeadline => analysis.deadline != null;
  bool get isCritical => analysis.priority == PriorityLevel.critical;
  bool get isSnoozed => userState.isSnoozed;

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
    'received_at': receivedAt.toIso8601String(),
    'is_unread': isUnread,
    'labels': labels,
    'analysis': analysis.toJson(),
    'user_state': userState.toJson(),
  };
}
