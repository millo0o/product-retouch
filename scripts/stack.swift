// 여러 이미지를 같은 폭으로 맞춰 위아래로 쌓는다 (원본 줄/보정본 줄 비교용).
// 사용법: stack <out.png> <폭> <그림1> <그림2> ...   그림 두 장마다 한 쌍(사이 간격 작게), 쌍과 쌍 사이엔 회색 굵은 줄
import Foundation; import CoreImage
let a = CommandLine.arguments
guard a.count >= 4, let W = Double(a[2]) else { print("사용법: stack out 폭 그림..."); exit(1) }
let cs = CGColorSpace(name: CGColorSpace.sRGB)!
let ctx = CIContext(options: [.workingColorSpace: cs])
var imgs: [CIImage] = []
for p in a[3...] {
    guard var i = CIImage(contentsOf: URL(fileURLWithPath: p)) else { continue }
    let s = W / i.extent.width
    i = i.applyingFilter("CILanczosScaleTransform", parameters: ["inputScale": s, "inputAspectRatio": 1])
    imgs.append(i.transformed(by: CGAffineTransform(translationX: -i.extent.minX, y: -i.extent.minY)))
}
let gapIn = 16.0, gapPair = 70.0
var total = 0.0
for (k, i) in imgs.enumerated() { total += Double(i.extent.height) + (k == imgs.count - 1 ? 0 : (k % 2 == 0 ? gapIn : gapPair)) }
let canvas = CGRect(x: 0, y: 0, width: W, height: total)
var out = CIImage(color: .white).cropped(to: canvas)
var y = total
for (k, i) in imgs.enumerated() {
    y -= Double(i.extent.height)
    out = i.transformed(by: CGAffineTransform(translationX: 0, y: CGFloat(y))).composited(over: out)
    if k < imgs.count - 1 {
        let g = k % 2 == 0 ? gapIn : gapPair
        if k % 2 == 1 {   // 쌍 사이 회색 줄
            let bar = CIImage(color: CIColor(red: 0.6, green: 0.6, blue: 0.6)).cropped(to: CGRect(x: 0, y: y - g / 2 - 3, width: W, height: 6))
            out = bar.composited(over: out)
        }
        y -= g
    }
}
try ctx.writePNGRepresentation(of: out.cropped(to: canvas), to: URL(fileURLWithPath: a[1]), format: .RGBA8, colorSpace: cs)
