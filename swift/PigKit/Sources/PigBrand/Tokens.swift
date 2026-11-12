// Generated from Pig family 1.9.0; do not edit.
import SwiftUI

public enum PigBrand {
    public static func canvas(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(red: 0.08235294, green: 0.06666667, blue: 0.10196078) : Color(red: 0.98431373, green: 0.97254902, blue: 0.98039216)
    }
    public static func surface(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(red: 0.11764706, green: 0.09803922, blue: 0.14509804) : Color(red: 1.00000000, green: 1.00000000, blue: 1.00000000)
    }
    public static func raised(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(red: 0.14509804, green: 0.11764706, blue: 0.18039216) : Color(red: 0.94901961, green: 0.92549020, blue: 0.94117647)
    }
    public static func text(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(red: 0.96078431, green: 0.94509804, blue: 0.95686275) : Color(red: 0.10980392, green: 0.08627451, blue: 0.12549020)
    }
    public static func secondary(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(red: 0.65490196, green: 0.62352941, blue: 0.68235294) : Color(red: 0.36078431, green: 0.32549020, blue: 0.39215686)
    }
    public static func accent(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(red: 0.93725490, green: 0.47843137, blue: 0.68235294) : Color(red: 0.83921569, green: 0.20000000, blue: 0.42352941)
    }
    public static func onAccent(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(red: 0.26666667, green: 0.16470588, blue: 0.09803922) : Color(red: 1.00000000, green: 1.00000000, blue: 1.00000000)
    }
    public static func hover(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(red: 0.96470588, green: 0.71764706, blue: 0.83137255) : Color(red: 0.69019608, green: 0.13725490, blue: 0.33725490)
    }
    public static func border(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(red: 0.18039216, green: 0.14901961, blue: 0.20392157) : Color(red: 0.88627451, green: 0.85098039, blue: 0.87843137)
    }
    public static func controlBorder(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(red: 0.49411765, green: 0.45882353, blue: 0.53725490) : Color(red: 0.52156863, green: 0.48235294, blue: 0.55294118)
    }
    public static func success(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(red: 0.06274510, green: 0.72549020, blue: 0.50588235) : Color(red: 0.05882353, green: 0.54117647, blue: 0.37254902)
    }
    public static func warning(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(red: 0.96078431, green: 0.61960784, blue: 0.04313725) : Color(red: 0.70588235, green: 0.41176471, blue: 0.05490196)
    }
    public static func danger(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(red: 0.93725490, green: 0.26666667, blue: 0.26666667) : Color(red: 0.76078431, green: 0.21568627, blue: 0.17254902)
    }
    public static func textAccent(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color(red: 0.93725490, green: 0.47843137, blue: 0.68235294) : Color(red: 0.69019608, green: 0.13725490, blue: 0.33725490)
    }
}

public enum PigMixArtwork {
    public static func colours(_ kind: String) -> (background: Color, foreground: Color) {
        switch kind {
        case "daily": return (Color(red: 0.26666667, green: 0.16470588, blue: 0.09803922), Color(red: 0.96470588, green: 0.71764706, blue: 0.83137255))
        case "artist": return (Color(red: 0.96470588, green: 0.71764706, blue: 0.83137255), Color(red: 0.26666667, green: 0.16470588, blue: 0.09803922))
        case "genre": return (Color(red: 0.08235294, green: 0.06666667, blue: 0.10196078), Color(red: 0.93725490, green: 0.47843137, blue: 0.68235294))
        case "popular": return (Color(red: 0.91764706, green: 0.31372549, blue: 0.28235294), Color(red: 0.26666667, green: 0.16470588, blue: 0.09803922))
        case "back": return (Color(red: 0.94901961, green: 0.92549020, blue: 0.94117647), Color(red: 0.83921569, green: 0.20000000, blue: 0.42352941))
        case "radio": return (Color(red: 0.83921569, green: 0.20000000, blue: 0.42352941), Color(red: 1.00000000, green: 1.00000000, blue: 1.00000000))
        case "fresh": return (Color(red: 0.97254902, green: 0.78039216, blue: 0.86666667), Color(red: 0.26666667, green: 0.16470588, blue: 0.09803922))
        case "releases": return (Color(red: 0.35294118, green: 0.22352941, blue: 0.14901961), Color(red: 0.97254902, green: 0.78039216, blue: 0.86666667))
        case "decade": return (Color(red: 0.91764706, green: 0.31372549, blue: 0.28235294), Color(red: 1.00000000, green: 1.00000000, blue: 1.00000000))
        case "deep": return (Color(red: 0.08235294, green: 0.06666667, blue: 0.10196078), Color(red: 0.96470588, green: 0.71764706, blue: 0.83137255))
        case "recent": return (Color(red: 1.00000000, green: 1.00000000, blue: 1.00000000), Color(red: 0.26666667, green: 0.16470588, blue: 0.09803922))
        default: return colours("daily")
        }
    }
}

public enum PigType {
    public static let caption: Font = .caption
    public static let footnote: Font = .footnote
    public static let subhead: Font = .subheadline
    public static let body: Font = .body
    public static let headline: Font = .title3
    public static let title: Font = .title
    public static let display: Font = .largeTitle
}
