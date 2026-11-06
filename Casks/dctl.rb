cask "dctl" do
  arch arm: "arm64", intel: "x86_64"

  version "0.4.6"
  sha256 arm:   "ca0dfe33981ec3974df4114f37e3fe23403fa25d2749cc1f232ee218ff3e43b2",
         intel: "5821498367b464f48dc05a4e7351276b2cc7a188c73281112fa1320409545117"

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
