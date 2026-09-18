class AppUserDto {
  final int id;
  final String? googleEmail;
  final String? displayName;

  const AppUserDto({
    required this.id,
    this.googleEmail,
    this.displayName,
  });

  factory AppUserDto.fromJson(Map<String, dynamic> json) {
    return AppUserDto(
      id: (json['id'] as num?)?.toInt() ?? 0,
      googleEmail: json['google_email'] as String?,
      displayName: json['display_name'] as String?,
    );
  }

  String get label => (displayName?.isNotEmpty ?? false)
      ? displayName!
      : (googleEmail ?? 'Signed in');
}
