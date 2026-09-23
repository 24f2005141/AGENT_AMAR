class AiModeOptionDto {
  final String id;
  final String label;
  final bool available;
  final String? model;
  final String? detail;

  const AiModeOptionDto({
    required this.id,
    required this.label,
    required this.available,
    this.model,
    this.detail,
  });

  factory AiModeOptionDto.fromJson(Map<String, dynamic> json) =>
      AiModeOptionDto(
        id: json['id'] as String,
        label: json['label'] as String? ?? json['id'] as String,
        available: json['available'] as bool? ?? false,
        model: json['model'] as String?,
        detail: json['detail'] as String?,
      );
}

class AiModeDto {
  final String selected;
  final List<AiModeOptionDto> options;

  const AiModeDto({required this.selected, required this.options});

  factory AiModeDto.fromJson(Map<String, dynamic> json) => AiModeDto(
    selected: json['selected'] as String? ?? 'conventional',
    options: (json['options'] as List<dynamic>? ?? const [])
        .map((item) => AiModeOptionDto.fromJson(item as Map<String, dynamic>))
        .toList(growable: false),
  );
}
