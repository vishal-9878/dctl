cask "dctl" do
  arch arm: "arm64", intel: "x86_64"

  version "0.4.9"
  sha256 arm:   "cd2a38c9d462a43e5e1c7c9094dd7a5e80fea902ea77a5bb3d8136322ef12ce7",
         intel: "2cc0a3390f6f62654db418b0f40bc6d617ff811e80f036ef3e2e0d939761efd5"

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
