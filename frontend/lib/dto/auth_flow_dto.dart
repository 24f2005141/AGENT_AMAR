import 'app_user_dto.dart';

/// Response of `POST /api/v1/auth/google/start`.
class GoogleAuthStartDto {
  final String authorizationUrl;
  final String flowId;

  const GoogleAuthStartDto({required this.authorizationUrl, required this.flowId});

  factory GoogleAuthStartDto.fromJson(Map<String, dynamic> json) => GoogleAuthStartDto(
        authorizationUrl: json['authorization_url'] as String? ?? '',
        flowId: json['flow_id'] as String? ?? '',
      );
}

/// Ready result of `GET /api/v1/auth/google/session?flow_id=`.
class GoogleSessionDto {
  final String sessionToken;
  final AppUserDto user;

  const GoogleSessionDto({required this.sessionToken, required this.user});

  factory GoogleSessionDto.fromJson(Map<String, dynamic> json) => GoogleSessionDto(
        sessionToken: json['session_token'] as String? ?? '',
        user: AppUserDto.fromJson(
            (json['user'] as Map<String, dynamic>?) ?? const {}),
      );
}

/// Response of `GET /api/v1/auth/me`.
class AuthMeDto {
  final AppUserDto user;
  final bool gmailConnected;

  const AuthMeDto({required this.user, required this.gmailConnected});

  factory AuthMeDto.fromJson(Map<String, dynamic> json) => AuthMeDto(
        user: AppUserDto.fromJson(
            (json['user'] as Map<String, dynamic>?) ?? const {}),
        gmailConnected: json['gmail_connected'] as bool? ?? false,
      );
}
