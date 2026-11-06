cask "dctl" do
  arch arm: "arm64", intel: "x86_64"

  version "0.4.8"
  sha256 arm:   "607304662abc85e7911c712ae50194bcb972bb9e4ceadfca2c3f3bd2ea97bf1c",
         intel: "7502bf0ea2a9d8ccec3463c1c828ec59b847dc765ba26d9434c555e9ebd4bc39"

  url "https://github.com/vishal-9878/dctl/releases/download/v#{version}/dctl-#{version}-macos-#{arch}.pkg"
  name "dctl"
  desc "Agentless desktop app for Docker and PM2 on many servers, over SSH"
  homepage "https://github.com/vishal-9878/dctl"

  livecheck do
    url :url
    strategy :github_latest
  end

  pkg "dctl-#{version}-macos-#{arch}.pkg"

  uninstall pkgutil: "io.github.vishal-9878.dctl",
            delete:  [
              "/Applications/dctl.app",
              "/usr/local/bin/dctl",
              "/usr/local/bin/dctl-ui",
              "/usr/local/lib/dctl",
            ]

  zap trash: [
    "~/.cache/dctl",
    "~/.config/dctl",
  ]
end
