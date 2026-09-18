import '../models/agent_analysis.dart';

/// Response of `GET /api/v1/emails/{email_id}/full`.
///
/// [body] is ALWAYS plain text — the backend flattens any HTML at intake
/// (`html_to_text` drops `<script>`/`<style>`/every tag), so it can never carry
/// executable markup. [bodyFormat] is `text` or `html_converted`.
class FullEmailDto {
  final String emailId;
  final String? threadId;
  final String subject;
  final String? senderName;
  final String senderEmail;
  final DateTime? receivedAt;
  final String body;
  final String bodyFormat;
  final bool isTruncated;
  final PrimaryCategory primaryCategory;
  final String finalCategory;
  final String priorityLevel;
  final bool actionRequired;

  const FullEmailDto({
    required this.emailId,
    this.threadId,
    required this.subject,
    this.senderName,
    required this.senderEmail,
    this.receivedAt,
    required this.body,
    this.bodyFormat = 'text',
    this.isTruncated = false,
    this.primaryCategory = PrimaryCategory.lowPriority,
    this.finalCategory = 'OTHER',
    this.priorityLevel = 'LOW',
    this.actionRequired = false,
  });

  bool get isHtmlConverted => bodyFormat == 'html_converted';

  factory FullEmailDto.fromJson(Map<String, dynamic> json) => FullEmailDto(
        emailId: json['email_id'] as String? ?? '',
        threadId: json['thread_id'] as String?,
        subject: json['subject'] as String? ?? '(no subject)',
        senderName: json['sender_name'] as String?,
        senderEmail: json['sender_email'] as String? ?? '',
        receivedAt: json['received_at'] != null
            ? DateTime.tryParse(json['received_at'] as String)
            : null,
        body: json['body'] as String? ?? '',
        bodyFormat: json['body_format'] as String? ?? 'text',
        isTruncated: json['is_truncated'] as bool? ?? false,
        primaryCategory: PrimaryCategory.fromWire(json['primary_category'] as String?),
        finalCategory: json['final_category'] as String? ?? 'OTHER',
        priorityLevel: json['priority_level'] as String? ?? 'LOW',
        actionRequired: json['action_required'] as bool? ?? false,
      );
}
