import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

class AppColors {
  // Brand Palette
  static const Color primaryNavy = Color(0xFF1B3C53);
  static const Color secondarySurface = Color(0xFF234C6A);
  static const Color mutedSlate = Color(0xFF456882);
  static const Color warmBeige = Color(0xFFD2C1B6);
  
  // Background & Surfaces
  static const Color background = Color(0xFF0C141B);
  static const Color surface = Color(0xFF142838);
  static const Color surfaceCard = Color(0xFF1B364B);
  static const Color surfaceElevated = Color(0xFF234C6A);
  static const Color border = Color(0x73456882);
  static const Color borderLight = Color(0x33456882);

  // Status & Priority Colors
  static const Color critical = Color(0xFFFF5C5C);
  static const Color high = Color(0xFFFF8E53);
  static const Color medium = Color(0xFFF6C90E);
  static const Color low = Color(0xFF7A9BB8);
  static const Color success = Color(0xFF38D39F);
  static const Color info = Color(0xFF64B5F6);

  // Text Colors
  static const Color textPrimary = Color(0xFFF0F4F8);
  static const Color textSecondary = Color(0xFF9CB3C9);
  static const Color textMuted = Color(0xFF6B889F);
  static const Color textDark = Color(0xFF1B3C53);
}

class AppTheme {
  static ThemeData get darkTheme {
    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      scaffoldBackgroundColor: AppColors.background,
      primaryColor: AppColors.warmBeige,
      colorScheme: const ColorScheme.dark(
        primary: AppColors.warmBeige,
        onPrimary: AppColors.textDark,
        secondary: AppColors.secondarySurface,
        onSecondary: AppColors.textPrimary,
        surface: AppColors.surface,
        onSurface: AppColors.textPrimary,
        error: AppColors.critical,
        onError: AppColors.textPrimary,
      ),
      cardTheme: CardThemeData(
        color: AppColors.surfaceCard,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: const BorderSide(color: AppColors.border, width: 1),
        ),
      ),
      appBarTheme: AppBarTheme(
        backgroundColor: AppColors.background,
        elevation: 0,
        centerTitle: false,
        titleTextStyle: GoogleFonts.bricolageGrotesque(
          fontSize: 20,
          fontWeight: FontWeight.w700,
          color: AppColors.textPrimary,
        ),
        iconTheme: const IconThemeData(color: AppColors.textPrimary),
      ),
      bottomNavigationBarTheme: const BottomNavigationBarThemeData(
        backgroundColor: Color(0xF0142838),
        selectedItemColor: AppColors.warmBeige,
        unselectedItemColor: AppColors.textMuted,
        type: BottomNavigationBarType.fixed,
        elevation: 10,
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: AppColors.surface,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(20),
          side: const BorderSide(color: AppColors.border, width: 1),
        ),
      ),
      bottomSheetTheme: const BottomSheetThemeData(
        backgroundColor: AppColors.surface,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
          side: BorderSide(color: AppColors.border, width: 1),
        ),
      ),
    );
  }

  // Typography Getters
  static TextStyle brandTitle({double fontSize = 24, FontWeight fontWeight = FontWeight.w800, Color color = AppColors.textPrimary}) {
    return GoogleFonts.bricolageGrotesque(
      fontSize: fontSize,
      fontWeight: fontWeight,
      color: color,
      letterSpacing: -0.5,
    );
  }

  static TextStyle heading({double fontSize = 18, FontWeight fontWeight = FontWeight.w700, Color color = AppColors.textPrimary}) {
    return GoogleFonts.bricolageGrotesque(
      fontSize: fontSize,
      fontWeight: fontWeight,
      color: color,
      letterSpacing: -0.3,
    );
  }

  static TextStyle body({double fontSize = 14, FontWeight fontWeight = FontWeight.w400, Color color = AppColors.textPrimary, double? height}) {
    return GoogleFonts.inter(
      fontSize: fontSize,
      fontWeight: fontWeight,
      color: color,
      height: height,
    );
  }

  static TextStyle bodyMedium({double fontSize = 13, FontWeight fontWeight = FontWeight.w500, Color color = AppColors.textSecondary}) {
    return GoogleFonts.inter(
      fontSize: fontSize,
      fontWeight: fontWeight,
      color: color,
    );
  }

  static TextStyle label({double fontSize = 11, FontWeight fontWeight = FontWeight.w600, Color color = AppColors.textSecondary, double letterSpacing = 0.5}) {
    return GoogleFonts.inter(
      fontSize: fontSize,
      fontWeight: fontWeight,
      color: color,
      letterSpacing: letterSpacing,
    );
  }

  static TextStyle mono({double fontSize = 12, FontWeight fontWeight = FontWeight.w600, Color color = AppColors.textPrimary}) {
    return GoogleFonts.jetBrainsMono(
      fontSize: fontSize,
      fontWeight: fontWeight,
      color: color,
    );
  }

  static TextStyle countdown({double fontSize = 14, FontWeight fontWeight = FontWeight.w700, Color color = AppColors.critical}) {
    return GoogleFonts.jetBrainsMono(
      fontSize: fontSize,
      fontWeight: fontWeight,
      color: color,
      letterSpacing: 0.5,
    );
  }
}
