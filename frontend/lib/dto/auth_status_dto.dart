class AuthStatusDto {
  final bool connected;
  final String provider;
  final String? accountEmail;
  final List<String> scopes;

  const AuthStatusDto({
    required this.connected,
    required this.provider,
    this.accountEmail,
    this.scopes = const [],
  });

  factory AuthStatusDto.fromJson(Map<String, dynamic> json) {
    return AuthStatusDto(
      connected: json['connected'] as bool? ?? false,
      provider: json['provider'] as String? ?? 'gmail',
      accountEmail: json['account_email'] as String?,
      scopes: (json['scopes'] as List<dynamic>?)
              ?.map((e) => e.toString())
              .toList() ??
          const [],
    );
  }

  Map<String, dynamic> toJson() => {
        'connected': connected,
        'provider': provider,
        'account_email': accountEmail,
        'scopes': scopes,
      };
}
